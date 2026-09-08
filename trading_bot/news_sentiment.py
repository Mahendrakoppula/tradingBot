import datetime as dt
import email.utils
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger(__name__)

# All verified live and keyless 2026-09-08 (real HTTP 200 + recent pubDate,
# not just a reachable URL - MoneyControl's RSS was checked too and dropped:
# it responds but every item was from April 2024, dead/abandoned feed).
# "india_market" = market-level India news; "global" = broader
# geopolitical/macro (wars, elections, central bank policy, trade
# negotiations) - explicit user request to cover more than just market news.
FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "india_market"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss", "india_market"),
    ("BBC World News", "https://feeds.bbci.co.uk/news/world/rss.xml", "global"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "global"),
    ("Business Standard World", "https://www.business-standard.com/rss/world-102.rss", "global"),
    ("Business Standard Economy & Policy", "https://www.business-standard.com/rss/economy-policy-102.rss", "global"),
]

FEED_TIMEOUT_SECONDS = 10
FEED_FETCH_SLEEP_SECONDS = 0.5  # polite pacing across 6 different hosts
MAX_HEADLINE_AGE_HOURS = 18  # roughly "since last close" - stale backlog isn't today's sentiment

# Deliberately simple substring matching on lowercased headlines, not NLP -
# explicit user request ("simple keyword/headline-based, not full NLP").
# CAUTION_KEYWORDS are high-impact/hard-to-score-directionally events (a war
# headline isn't reliably bullish or bearish for Indian equities) - flagged
# as a caution signal rather than forced into a positive/negative score,
# same philosophy premarket_bias.py already uses for scheduled economic
# events (any event -> CAUTIOUS, not a guessed direction).
POSITIVE_KEYWORDS = [
    "rate cut", "cuts rates", "dovish", "stimulus", "trade deal", "trade agreement",
    "ceasefire", "peace deal", "tariffs lifted", "tariffs removed", "rally", "surge",
    "record high", "recovery", "growth beats", "upgrade", "bailout agreed",
]
NEGATIVE_KEYWORDS = [
    "rate hike", "hikes rates", "raises rates", "basis points", "hawkish", "tightening",
    "trade war", "trade tensions", "tariffs imposed", "sanctions", "crash", "selloff",
    "plunge", "recession", "default", "downgrade", "inflation surge", "shutdown",
]
CAUTION_KEYWORDS = [
    "war", "invasion", "military strike", "missile", "attack", "conflict", "coup",
    "election", "political crisis", "impeachment", "emergency", "crisis", "collapse",
]


def _fetch_feed_headlines(name: str, url: str) -> list[dict]:
    """Returns [{"title":, "link":, "published_at": datetime|None}, ...] for
    one RSS feed, filtered to MAX_HEADLINE_AGE_HOURS. Never raises - a
    single dead/slow feed must not break the whole sentiment scan."""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=FEED_TIMEOUT_SECONDS)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        log.exception("News sentiment: feed fetch/parse failed for %s", name)
        return []

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_HEADLINE_AGE_HOURS)
    headlines = []
    for item in root.iter("item"):
        title = item.findtext("title")
        if not title:
            continue
        pub_raw = item.findtext("pubDate")
        published_at = None
        if pub_raw:
            try:
                published_at = email.utils.parsedate_to_datetime(pub_raw)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=dt.timezone.utc)
            except Exception:
                published_at = None
        if published_at is not None and published_at < cutoff:
            continue
        headlines.append({"title": title.strip(), "link": item.findtext("link"), "published_at": published_at})
    return headlines


def _score_headline(title: str) -> tuple[int, list[str], bool]:
    """Returns (score, matched_keywords, is_caution). score = positive hits
    minus negative hits; is_caution = True if any caution keyword matched
    (checked independently of score, since a caution headline may also
    contain positive/negative words).

    Matches on WORD boundaries, not raw substrings - verified live
    2026-09-08 that plain `kw in lowered` false-positives badly: "ESDS
    Software shares skyrocket" matched the "war" caution keyword purely
    because "software" contains "war". Same risk for "warranty", "reward",
    "toward", "warehouse", etc. against any single-word keyword.
    """
    lowered = title.lower()
    matched = []
    score = 0
    for kw in POSITIVE_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", lowered):
            matched.append(f"+{kw}")
            score += 1
    for kw in NEGATIVE_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", lowered):
            matched.append(f"-{kw}")
            score -= 1
    is_caution = any(re.search(rf"\b{re.escape(kw)}\b", lowered) for kw in CAUTION_KEYWORDS)
    if is_caution:
        matched.append("!caution")
    return score, matched, is_caution


def compute_news_sentiment() -> dict:
    """Scans all FEEDS, scores every recent headline by simple keyword
    matching, and returns an aggregate read. Never raises - a feed outage
    degrades gracefully to fewer headlines scanned, not a crash.

    NOT wired into any trading decision yet (see config.py's news_sentiment_*
    comment) - explicit user request to collect and log first, the same
    "prove it before gating" pattern already used for IV/PCR. Included in
    the pre-market bias notification as an informational read only.
    """
    all_scored = []
    for name, url, category in FEEDS:
        for h in _fetch_feed_headlines(name, url):
            score, matched, is_caution = _score_headline(h["title"])
            if score != 0 or is_caution:
                all_scored.append({
                    "source": name, "category": category, "title": h["title"], "link": h["link"],
                    "published_at": h["published_at"].isoformat() if h["published_at"] else None,
                    "score": score, "matched_keywords": matched, "is_caution": is_caution,
                })
        time.sleep(FEED_FETCH_SLEEP_SECONDS)

    total_score = sum(h["score"] for h in all_scored)
    caution_headlines = [h for h in all_scored if h["is_caution"]]

    if caution_headlines:
        label = "CAUTIOUS"
    elif total_score >= 2:
        label = "POSITIVE"
    elif total_score <= -2:
        label = "NEGATIVE"
    else:
        label = "NEUTRAL"

    # Keep only the most notable headlines in the returned/logged record -
    # sorted by |score| then caution first, capped so this doesn't grow
    # unbounded on a busy news day.
    notable = sorted(all_scored, key=lambda h: (not h["is_caution"], -abs(h["score"])))[:15]

    return {
        "computed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "total_score": total_score,
        "caution_count": len(caution_headlines),
        "feeds_scanned": len(FEEDS),
        "matched_headlines": notable,
    }


def format_news_sentiment_line(sentiment: dict) -> str:
    parts = [f"News sentiment: {sentiment['label']} (score {sentiment['total_score']:+d}"]
    if sentiment["caution_count"]:
        parts.append(f", {sentiment['caution_count']} high-impact headline(s)")
    parts.append(")")
    top = sentiment["matched_headlines"][:3]
    if top:
        parts.append(" - " + "; ".join(f"{h['title']} ({h['source']})" for h in top))
    return "".join(parts)
