import datetime as dt
import email.utils
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

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

# CAUTION_KEYWORDS: high-impact/hard-to-score-directionally events (a war
# headline isn't reliably bullish or bearish for Indian equities) - flagged
# as a caution signal rather than forced into a positive/negative score,
# same philosophy premarket_bias.py already uses for scheduled economic
# events (any event -> CAUTIOUS, not a guessed direction). Word-boundary
# matched, not raw substring - verified live 2026-09-08 that plain
# `kw in lowered` false-positives badly: "ESDS Software shares skyrocket"
# matched "war" purely because "software" contains "war". Same risk for
# "warranty", "reward", "toward", "warehouse", etc. against any
# single-word keyword.
CAUTION_KEYWORDS = [
    "war", "invasion", "military strike", "missile", "attack", "conflict", "coup",
    "election", "political crisis", "impeachment", "emergency", "crisis", "collapse",
]

# VADER is a general-purpose sentiment lexicon - verified live 2026-09-08
# that plain VADER scores "RBI hikes rates by 50 basis points, markets
# selloff" as perfectly NEUTRAL (compound 0.0), because "hikes", "basis
# points", "selloff" aren't everyday-language sentiment words it knows.
# This extension teaches it finance vocabulary while keeping VADER's real
# NLP behavior (negation, degree modifiers, punctuation/caps intensity) -
# standard lexicon-tuning practice, not a hack. Scores are on VADER's
# native -4..+4 valence scale.
FINANCE_LEXICON = {
    "hike": -1.5, "hikes": -1.5, "hiked": -1.5, "hiking": -1.5,
    "selloff": -2.5, "sell-off": -2.5, "plunge": -2.8, "plunges": -2.8, "plunged": -2.8,
    "hawkish": -1.5, "tightening": -1.2, "recession": -2.5, "default": -2.0,
    "sanctions": -1.8, "downgrade": -1.8, "downgraded": -1.8, "crash": -3.0, "crashes": -3.0,
    "tariff": -1.2, "tariffs": -1.2, "shutdown": -1.5, "layoffs": -1.8,
    "rally": 2.0, "rallies": 2.0, "rallied": 2.0, "surge": 2.2, "surges": 2.2, "surged": 2.2,
    "dovish": 1.5, "stimulus": 1.5, "upgrade": 1.8, "upgraded": 1.8,
    "skyrocket": 2.5, "skyrockets": 2.5, "ceasefire": 1.8, "bailout": 1.2,
}

_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(FINANCE_LEXICON)

# VADER's own convention: |compound| < 0.05 is effectively neutral noise,
# not a real signal - don't let near-zero scores clutter matched_headlines.
NOTABLE_COMPOUND_THRESHOLD = 0.05


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


def _score_headline(title: str) -> tuple[float, list[str], bool]:
    """Returns (compound, matched_keywords, is_caution). compound is
    VADER's own -1..+1 sentiment score (using the finance-extended lexicon
    above), which properly handles negation ("not gains"), degree
    modifiers ("massive surge" vs "surge"), and punctuation/caps intensity
    - real NLP behavior, not substring counting. is_caution is a SEPARATE
    check (word-boundary keyword match, not sentiment) for high-impact
    events VADER has no business trying to score directionally.
    """
    lowered = title.lower()
    compound = _analyzer.polarity_scores(title)["compound"]
    matched = [f"vader={compound:+.2f}"] if abs(compound) >= NOTABLE_COMPOUND_THRESHOLD else []
    is_caution = any(re.search(rf"\b{re.escape(kw)}\b", lowered) for kw in CAUTION_KEYWORDS)
    if is_caution:
        matched.append("!caution")
    return compound, matched, is_caution


def compute_news_sentiment() -> dict:
    """Scans all FEEDS, scores every recent headline with VADER (finance-
    extended lexicon - see FINANCE_LEXICON) plus a separate caution-keyword
    check, and returns an aggregate read. Never raises - a feed outage
    degrades gracefully to fewer headlines scanned, not a crash.

    NOT wired into any trading decision yet (see config.py's news_sentiment_*
    comment) - explicit user request to collect and log first, the same
    "prove it before gating" pattern already used for IV/PCR. Included in
    the pre-market bias notification as an informational read only.
    """
    all_scored = []
    for name, url, category in FEEDS:
        for h in _fetch_feed_headlines(name, url):
            compound, matched, is_caution = _score_headline(h["title"])
            if abs(compound) >= NOTABLE_COMPOUND_THRESHOLD or is_caution:
                all_scored.append({
                    "source": name, "category": category, "title": h["title"], "link": h["link"],
                    "published_at": h["published_at"].isoformat() if h["published_at"] else None,
                    "score": compound, "matched_keywords": matched, "is_caution": is_caution,
                })
        time.sleep(FEED_FETCH_SLEEP_SECONDS)

    # MEAN, not sum, of every notable headline's compound score -
    # deliberately, found live 2026-09-08 that summing inflates purely with
    # headline VOLUME: a busy news day produces a large number regardless
    # of whether sentiment is actually strong, making the score both
    # incomparable across days and blowing past any fixed threshold almost
    # by chance once more than a handful of headlines match. The mean stays
    # naturally bounded to roughly -1..+1 same as a single VADER score,
    # so it's directly comparable day to day.
    caution_headlines = [h for h in all_scored if h["is_caution"]]
    total_score = sum(h["score"] for h in all_scored) / len(all_scored) if all_scored else 0.0

    # Thresholds tuned for VADER's own -1..+1 scale (finance-extended
    # lexicon, see FINANCE_LEXICON above) - roughly VADER's own convention
    # for "not neutral" (0.05), scaled up a bit since this is an average
    # across many headlines, not one.
    if caution_headlines:
        label = "CAUTIOUS"
    elif total_score >= 0.15:
        label = "POSITIVE"
    elif total_score <= -0.15:
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
        "total_score": round(total_score, 3),
        "caution_count": len(caution_headlines),
        "feeds_scanned": len(FEEDS),
        "matched_headlines": notable,
    }


def format_news_sentiment_line(sentiment: dict) -> str:
    parts = [f"News sentiment: {sentiment['label']} (score {sentiment['total_score']:+.2f}"]
    if sentiment["caution_count"]:
        parts.append(f", {sentiment['caution_count']} high-impact headline(s)")
    parts.append(")")
    top = sentiment["matched_headlines"][:3]
    if top:
        parts.append(" - " + "; ".join(f"{h['title']} ({h['source']})" for h in top))
    return "".join(parts)
