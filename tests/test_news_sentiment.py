import datetime as dt
from unittest.mock import MagicMock, patch

from features.news_sentiment import (
    FEEDS,
    _score_headline,
    compute_news_sentiment,
    format_news_sentiment_line,
)


def _rss_xml(*titles_and_ages_hours: tuple[str, float]) -> bytes:
    now = dt.datetime.now(dt.timezone.utc)
    items = []
    for title, age_hours in titles_and_ages_hours:
        pub_date = (now - dt.timedelta(hours=age_hours)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        items.append(f"<item><title>{title}</title><link>http://example.com</link><pubDate>{pub_date}</pubDate></item>")
    return f"<rss><channel>{''.join(items)}</channel></rss>".encode()


def _mock_resp(content: bytes):
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def test_score_headline_positive_finance_term():
    compound, matched, is_caution = _score_headline("Markets rally sharply on strong earnings")
    assert compound > 0
    assert is_caution is False


def test_score_headline_negative_finance_term():
    compound, matched, is_caution = _score_headline("RBI hikes rates, markets selloff")
    assert compound < 0


def test_score_headline_caution_keyword_word_boundary_not_substring():
    """Regression check for the exact bug found live on `main`:
    'ESDS Software shares skyrocket' must NOT match the caution keyword
    'war' via the substring inside 'software'."""
    _, _, is_caution = _score_headline("ESDS Software shares skyrocket on new order win")
    assert is_caution is False


def test_score_headline_caution_keyword_real_match():
    _, _, is_caution = _score_headline("Military strike reported near border, markets on edge")
    assert is_caution is True


def test_compute_news_sentiment_all_feeds_down_returns_neutral_empty():
    with patch("features.news_sentiment.requests.get", side_effect=Exception("all feeds down")), \
         patch("features.news_sentiment.time.sleep"):
        result = compute_news_sentiment()
    assert result["label"] == "NEUTRAL"
    assert result["total_score"] == 0.0
    assert result["caution_count"] == 0
    assert result["feeds_scanned"] == len(FEEDS)
    assert result["matched_headlines"] == []


def test_compute_news_sentiment_positive_headlines_give_positive_label():
    xml = _rss_xml(("Markets rally sharply as stocks surge to record highs", 1))
    with patch("features.news_sentiment.requests.get", return_value=_mock_resp(xml)), \
         patch("features.news_sentiment.time.sleep"):
        result = compute_news_sentiment()
    assert result["label"] == "POSITIVE"
    assert result["total_score"] > 0


def test_compute_news_sentiment_caution_headline_forces_cautious_label_even_if_positive_elsewhere():
    xml = _rss_xml(
        ("Markets rally sharply on strong earnings", 1),
        ("Military strike reported, tensions escalate", 1),
    )
    with patch("features.news_sentiment.requests.get", return_value=_mock_resp(xml)), \
         patch("features.news_sentiment.time.sleep"):
        result = compute_news_sentiment()
    assert result["label"] == "CAUTIOUS"
    assert result["caution_count"] >= 1


def test_compute_news_sentiment_stale_headlines_are_excluded():
    xml = _rss_xml(("Markets rally sharply on strong earnings", 100))  # far older than MAX_HEADLINE_AGE_HOURS
    with patch("features.news_sentiment.requests.get", return_value=_mock_resp(xml)), \
         patch("features.news_sentiment.time.sleep"):
        result = compute_news_sentiment()
    assert result["matched_headlines"] == []
    assert result["label"] == "NEUTRAL"


def test_format_news_sentiment_line_includes_label_and_score():
    sentiment = {"label": "POSITIVE", "total_score": 0.42, "caution_count": 0, "matched_headlines": []}
    line = format_news_sentiment_line(sentiment)
    assert "POSITIVE" in line
    assert "+0.42" in line


def test_format_news_sentiment_line_includes_caution_count():
    sentiment = {"label": "CAUTIOUS", "total_score": -0.1, "caution_count": 2, "matched_headlines": []}
    line = format_news_sentiment_line(sentiment)
    assert "2 high-impact" in line
