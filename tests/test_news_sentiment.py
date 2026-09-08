import datetime as dt

import trading_bot.news_sentiment as ns
from trading_bot.news_sentiment import _score_headline, compute_news_sentiment, format_news_sentiment_line


def test_score_headline_positive():
    score, matched, caution = _score_headline("Fed signals rate cut, markets rally on stimulus hopes")
    assert score > 0
    assert caution is False


def test_score_headline_negative():
    score, matched, caution = _score_headline("RBI hikes rates by 50 basis points, markets selloff")
    assert score < 0
    assert caution is False


def test_score_headline_caution_overrides_direction():
    score, matched, caution = _score_headline("Military conflict escalates as war fears grow")
    assert caution is True
    assert "!caution" in matched


def test_score_headline_neutral_no_keywords():
    score, matched, caution = _score_headline("Company announces quarterly results")
    assert score == 0
    assert caution is False
    assert matched == []


def test_score_headline_word_boundary_avoids_substring_false_positives():
    # Regression: found live 2026-09-08 that plain substring matching
    # flagged this as a "war" caution headline purely because "software"
    # contains "war" - word-boundary matching must not repeat that.
    score, matched, caution = _score_headline("ESDS Software shares skyrocket 195% from IPO price")
    assert caution is False
    assert matched == []
    # A few more common false-positive traps for the same reason.
    for headline in ["Company signs new warranty agreement", "Investors reward strong earnings", "Firm moves toward profitability"]:
        _, _, c = _score_headline(headline)
        assert c is False, f"false positive caution match on: {headline}"


def _rss_xml(items: list[tuple[str, str]]) -> bytes:
    """items = [(title, pubDate_rfc822), ...]"""
    body = "".join(f"<item><title>{t}</title><pubDate>{p}</pubDate><link>http://x</link></item>" for t, p in items)
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'.encode()


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_compute_news_sentiment_positive_label(monkeypatch):
    now_rfc822 = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    monkeypatch.setattr(ns, "FEEDS", [("TestFeed", "http://example.com/rss", "global")])
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ns.requests, "get",
        lambda url, headers, timeout: FakeResponse(_rss_xml([
            ("Markets rally as trade deal reached", now_rfc822),
            ("Central bank signals dovish stimulus", now_rfc822),
        ])),
    )

    result = compute_news_sentiment()
    assert result["label"] == "POSITIVE"
    assert result["total_score"] >= 2
    assert result["caution_count"] == 0


def test_compute_news_sentiment_caution_label_wins_over_score(monkeypatch):
    now_rfc822 = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    monkeypatch.setattr(ns, "FEEDS", [("TestFeed", "http://example.com/rss", "global")])
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ns.requests, "get",
        lambda url, headers, timeout: FakeResponse(_rss_xml([
            ("Markets rally on stimulus hopes", now_rfc822),  # positive
            ("War breaks out, military conflict spreads", now_rfc822),  # caution
        ])),
    )

    result = compute_news_sentiment()
    assert result["label"] == "CAUTIOUS"
    assert result["caution_count"] == 1


def test_compute_news_sentiment_filters_stale_headlines(monkeypatch):
    stale_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    monkeypatch.setattr(ns, "FEEDS", [("TestFeed", "http://example.com/rss", "global")])
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ns.requests, "get",
        lambda url, headers, timeout: FakeResponse(_rss_xml([
            ("War fears grow as conflict escalates", stale_date),  # 3 days old - like the dead MoneyControl feed
        ])),
    )

    result = compute_news_sentiment()
    assert result["label"] == "NEUTRAL"  # the stale caution headline must not count
    assert result["caution_count"] == 0


def test_compute_news_sentiment_never_raises_on_feed_failure(monkeypatch):
    monkeypatch.setattr(ns, "FEEDS", [("BrokenFeed", "http://example.com/rss", "global")])
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)

    def broken_get(url, headers, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ns.requests, "get", broken_get)

    result = compute_news_sentiment()
    assert result["label"] == "NEUTRAL"
    assert result["matched_headlines"] == []


def test_format_news_sentiment_line_includes_top_headlines():
    sentiment = {
        "label": "NEGATIVE", "total_score": -3, "caution_count": 0,
        "matched_headlines": [{"title": "Rates rise sharply", "source": "TestFeed"}],
    }
    line = format_news_sentiment_line(sentiment)
    assert "NEGATIVE" in line
    assert "Rates rise sharply" in line
