"""PAPER-only NSE stock momentum book: research/stock_momentum.py's M2 rule, run forward.

M2 = 30 stocks ranked by 12-1 month momentum percentile + low-volatility percentile,
from the 200 most-traded NSE stocks, re-formed after each month's last trading day.
Backtest 2006 - Nov 2026 (survivorship-free, Rs.2L account, Angel One delivery costs):
14.9% a year net vs 10.9% for the NIFTY 50 price index, worst fall -58%; it did NOT clear
the pre-registered bar against an equal-weight top-200 universe (t 0.65). That is why this
is paper only: it never calls an order endpoint and has no broker session at all.
Pure Python (no pandas/numpy) so the oneshot stays small on the t3.micro.

Runs once each weekday morning (trading-bot-momentum.timer) and is idempotent:
  1. sync  - cache NSE's daily equity bhavcopy for the last ~300 trading days and read
             corporate actions from NSE's API (NSE first, the public GitHub mirror
             inquilabee/powernse second; a weekday both report missing after 3 days is a
             holiday).
  2. fill  - fill pending orders at the latest close after the formation day (normally
             the first trading day of the month).
  3. form  - after a month's last trading day, pick the new 30: names leaving are sold in
             full, the proceeds are split equally across names entering, continuing names
             are left alone. The very first formation uses the latest data and fills at
             the next close after the bot starts, never at a past price.
  4. report- every Monday, NAV against NIFTY 50 since inception, to the TECH channel.
Holdings keep the quantity bought; splits, bonuses and dividends are applied from the
corporate-action list at every valuation, so an action the mirror publishes late is
still counted.

    python -m trading_bot.momentum_paper run      # what the timer runs
    python -m trading_bot.momentum_paper status   # print the book, send nothing
"""
import argparse
import csv
import datetime as dt
import io
import json
import logging
import math
import re
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

from trading_bot import technical_notifier
from trading_bot.timeutil import now_ist

log = logging.getLogger("trading_bot.momentum_paper")

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".state"
CACHE_DIR = STATE_DIR / "momentum_cache"
LEDGER_PATH = STATE_DIR / "momentum_paper.json"

CAPITAL = 200_000.0
TOP_N, UNIVERSE_N, MIN_PRICE, MIN_VOL = 30, 200, 20.0, 0.08
HISTORY_DAYS = 300
BROKER_PCT, BROKER_CAP = 0.1, 20.0
STT, STAMP_BUY, EXCH, SEBI, GST, SLIP, DP = 0.1, 0.015, 0.00297, 0.0001, 18.0, 0.1, 23.60

# NSE endpoints and calling convention as used daily by the maintained powernse project
# (UDiFF bhavcopy since 8 Jul 2024; corporate actions in 7-day spans; home page first
# for cookies; a Referer; about 3 requests a second).
NSE_HOME = "https://www.nseindia.com/"
NSE_BHAV = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
NSE_INDEX = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
NSE_ACTIONS = "https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}"
MIRROR = "https://raw.githubusercontent.com/inquilabee/powernse/main/nse-data/raw"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/118.0",
           "Referer": "https://www.nseindia.com/", "Accept": "*/*"}
MIN_INTERVAL = 0.35
ACTION_SPAN_DAYS = 7

ETF_SYMBOL = re.compile(r"BEES|ETF|LIQUID|NIFTY|SENSEX|BANKEX|GILT|CASE$|^N100$|^NV20|^MON100$|^MAFANG$|^HNGSNG"
                        r"|GOLDSHARE|^QGOLD|^KOTAKGOLD$|^AXISGOLD$|^IDBIGOLD$|^RELGOLD$|^SBIGETS$|^GOLD1$")
ETF_NAME = re.compile(r"ETF|BEES|\bAMC\b|AMC\s*-|MUTUAL|-GR\b|\bFUND\b", re.I)
_NUM = r"(\d+(?:\.\d+)?)"


# ------------------------------------------------------------------ parsing
def parse_bhav(text: str) -> dict:
    """EQ-series rows of an NSE equity bhavcopy (legacy or UDiFF layout) ->
    {symbol: (close, previous close, traded value, is_etf)}."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if isinstance(v, str) or v is None}
        if "SYMBOL" in row:
            if row.get("SERIES") != "EQ":
                continue
            sym, close, prev, value, name = row["SYMBOL"], row["CLOSE"], row["PREVCLOSE"], row["TOTTRDVAL"], ""
        else:
            if row.get("SctySrs") != "EQ":
                continue
            sym, close, prev, value, name = row["TckrSymb"], row["ClsPric"], row["PrvsClsgPric"], row["TtlTrfVal"], row.get("FinInstrmNm", "")
        try:
            out[sym] = (float(close), float(prev), float(value or 0), bool(ETF_SYMBOL.search(sym) or ETF_NAME.search(name)))
        except ValueError:
            continue
    return out


def _write_cache(path: Path, day: dict) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sym", "close", "prev", "value", "etf"])
        for sym, (c, p, v, e) in day.items():
            w.writerow([sym, c, p, v, int(e)])


def _read_cache(path: Path) -> dict:
    with path.open() as fh:
        return {r["sym"]: (float(r["close"]), float(r["prev"]), float(r["value"]), r["etf"] == "1") for r in csv.DictReader(fh)}


def parse_actions(records: list) -> dict:
    """NSE corporate-action records -> {(symbol, ex_date): (price_factor, dividend_rs)}.
    Split X->Y: price x Y/X. Bonus a:b: price x b/(a+b). Dividends in rupees, or % of face value."""
    acts = defaultdict(lambda: [1.0, 0.0])
    seen = set()
    for r in records:
        s, sym, ex = (r.get("subject") or ""), (r.get("symbol") or "").strip(), r.get("exDate")
        try:
            exd = dt.datetime.strptime(ex, "%d-%b-%Y").date()
        except (TypeError, ValueError):
            continue
        low = " ".join(s.lower().split())
        if not sym or (sym, exd, low) in seen:
            continue
        seen.add((sym, exd, low))
        a = acts[(sym, exd)]
        for m in re.finditer(r"bonus\s*" + _NUM + r"\s*:\s*" + _NUM, low):
            x, y = float(m.group(1)), float(m.group(2))
            if x > 0 and y > 0:
                a[0] *= y / (x + y)
        if re.search(r"split|sub-division|sub division|consolidat", low):
            nums = [float(n) for n in re.findall(r"r[se]\.?\s*" + _NUM, low)]
            if len(nums) >= 2 and nums[0] > 0 and nums[1] > 0:
                a[0] *= nums[1] / nums[0]
        for m in re.finditer(r"dividend[^;/]*?r[se]\.?\s*" + _NUM, low):
            a[1] += float(m.group(1))
        for m in re.finditer(r"dividend\s*-?\s*" + _NUM + r"\s*%", low):
            try:
                a[1] += float(m.group(1)) / 100 * float(r.get("faceVal") or 0)
            except ValueError:
                pass
    return {k: (v[0], v[1]) for k, v in acts.items()}


# ------------------------------------------------------------------ data
class Source:
    """Fetches one day's files; the cache makes every run after the first cheap."""

    def __init__(self, cache: Path = CACHE_DIR, session: requests.Session | None = None):
        self.cache = cache
        self.http = session or requests.Session()
        self._primed = False
        self._last = 0.0

    def _get(self, url: str, accept: str = "*/*") -> tuple[bytes | None, bool]:
        """(content, definitely_missing): only an HTTP 404 counts as definitely missing; an
        HTML page where a file or JSON was expected (NSE's block page) counts as a failure."""
        if "nseindia.com" in url and not self._primed:
            self._primed = True
            try:  # cookies, best effort
                self.http.get(NSE_HOME, headers={**HEADERS, "Accept": "text/html"}, timeout=30)
            except requests.RequestException as e:
                log.info("NSE home page priming failed: %s", e)
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            r = self.http.get(url, headers={**HEADERS, "Accept": accept}, timeout=30)
        except requests.RequestException as e:
            log.info("fetch failed %s: %s", url, e)
            return None, False
        if r.status_code == 200 and r.content and not r.content.lstrip()[:1] == b"<":
            return r.content, False
        return None, r.status_code == 404

    def bhav(self, d: dt.date) -> dict | None:
        f = self.cache / "bhav" / f"{d}.csv"
        if f.exists():
            return _read_cache(f) if f.stat().st_size > 0 else None
        text = None
        raw, gone1 = self._get(NSE_BHAV.format(d=d))
        if raw:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    text = z.read(z.namelist()[0]).decode()
            except (zipfile.BadZipFile, IndexError, UnicodeDecodeError):
                text = None
        gone2 = False
        if text is None:
            raw, gone2 = self._get(f"{MIRROR}/bhavcopy/{d:%Y}/{d}.csv")
            text = raw.decode() if raw else None
        f.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            if gone1 and gone2 and (now_ist().date() - d).days > 3:
                f.write_text("")  # both sources say 404 days later: a holiday, don't refetch
            return None
        day = parse_bhav(text)
        _write_cache(f, day)
        return day

    def actions(self, d: dt.date) -> list:
        f = self.cache / "actions" / f"{d}.json"
        recent = (now_ist().date() - d).days <= 10  # the mirror can publish late: re-check
        if f.exists() and not (recent and f.read_text() in ("", "[]")):
            return json.loads(f.read_text() or "[]")
        raw, gone = self._get(f"{MIRROR}/corporate_actions/{d:%Y}/{d}.json")
        recs = json.loads(raw) if raw else []
        if raw or (gone and not recent):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(recs))
        return recs

    def actions_range(self, start: dt.date, end: dt.date) -> list:
        """Corporate actions with ex-dates in [start, end]: NSE's API in 7-day spans (spans
        more than 10 days old are cached), the mirror's per-day files for any span NSE refuses."""
        recs = []
        for a, b in spans(start, end):
            f = self.cache / "actions_nse" / f"{a}_{b}.json"
            if f.exists() and (now_ist().date() - b).days > 10:
                recs.extend(json.loads(f.read_text()))
                continue
            raw, _ = self._get(NSE_ACTIONS.format(a=a, b=b), accept="application/json")
            got = None
            if raw:
                try:
                    got = json.loads(raw)
                except ValueError:
                    got = None
            if isinstance(got, list):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(got))
                recs.extend(got)
            else:
                d = a
                while d <= b:
                    if d.weekday() < 5:
                        recs.extend(self.actions(d))
                    d += dt.timedelta(days=1)
        return recs

    def nifty(self, d: dt.date) -> float | None:
        for url in (NSE_INDEX.format(d=d), f"{MIRROR}/index_closes/{d:%Y}/{d}.csv"):
            raw, _ = self._get(url)
            if not raw:
                continue
            try:
                for row in csv.DictReader(io.StringIO(raw.decode(errors="ignore"))):
                    if (row.get("Index Name") or "").strip().lower() == "nifty 50":
                        return float(row["Closing Index Value"])
            except (ValueError, KeyError):
                continue
        return None


def spans(start: dt.date, end: dt.date, days: int = ACTION_SPAN_DAYS) -> list:
    out, a = [], start
    while a <= end:
        b = min(end, a + dt.timedelta(days=days - 1))
        out.append((a, b))
        a = b + dt.timedelta(days=1)
    return out


def sync(src: Source, today: dt.date, days: int = HISTORY_DAYS) -> dict:
    """{trading_day: {symbol: row}} for the last `days` weekdays that have a bhavcopy, before today."""
    out = {}
    d = today - dt.timedelta(days=1)
    tries = 0
    while len(out) < days and tries < days * 2:
        if d.weekday() < 5:
            day = src.bhav(d)
            if day:
                out[d] = day
        d -= dt.timedelta(days=1)
        tries += 1
    return dict(sorted(out.items()))


def series(bhavs: dict, acts: dict, upto: dt.date):
    """Adjusted daily returns and traded values per symbol over the cached days up to `upto`."""
    days = [d for d in bhavs if d <= upto]
    n = len(days)
    R = defaultdict(lambda: [None] * n)
    V = defaultdict(lambda: [0.0] * n)
    etf = set()
    for i, d in enumerate(days):
        for sym, (close, prev, value, is_etf) in bhavs[d].items():
            f, div = acts.get((sym, d), (1.0, 0.0))
            base = prev * f
            r = (close + div) / base - 1 if base > 0 else None
            R[sym][i] = None if r is None or r < -0.5 or r > 1.0 else r
            V[sym][i] = value
            if is_etf:
                etf.add(sym)
    return days, R, V, etf


def _pct_rank(values: list) -> list:
    """Percentile ranks with ties averaged (pandas rank(pct=True))."""
    order = sorted(range(len(values)), key=lambda j: values[j])
    ranks = [0.0] * len(values)
    k = 0
    while k < len(order):
        m = k
        while m + 1 < len(order) and values[order[m + 1]] == values[order[k]]:
            m += 1
        for j in order[k:m + 1]:
            ranks[j] = (k + m) / 2 + 1
        k = m + 1
    return [r / len(values) for r in ranks]


def select(days: list, R: dict, V: dict, etf: set, last: dict) -> list:
    """M2 picks from data up to the last day (the formation day)."""
    n = len(days)
    if n < 252:
        raise ValueError(f"need 252 trading days of history, have {n}")
    cand = []
    for sym, rs in R.items():
        if sym in etf or ETF_SYMBOL.search(sym) or sym not in last or last[sym][0] < MIN_PRICE:
            continue
        valid = [x for x in rs[n - 252:] if x is not None]
        if len(valid) < 240:
            continue
        vol = statistics.pstdev(valid)
        if vol * math.sqrt(252) < MIN_VOL:
            continue
        mom = math.prod(1 + (x or 0.0) for x in rs[n - 252:n - 21]) - 1
        cand.append((sym, statistics.median(V[sym][n - 126:]), mom, vol))
    uni = sorted(cand, key=lambda c: -c[1])[:UNIVERSE_N]
    score = [a + b for a, b in zip(_pct_rank([c[2] for c in uni]), _pct_rank([-c[3] for c in uni]))]
    return [uni[j][0] for j in sorted(range(len(uni)), key=lambda j: -score[j])[:TOP_N]]


# ------------------------------------------------------------------ ledger
def _rates(value: float) -> tuple[float, float]:
    """(buy, sell) cost as a fraction of traded value, slippage included, DP excluded."""
    brok = min(BROKER_PCT / 100 * value, BROKER_CAP) / value if value > 0 else 0.0
    common = brok + (STT + EXCH + SEBI + SLIP) / 100 + (brok + (EXCH + SEBI) / 100) * GST / 100
    return common + STAMP_BUY / 100, common


def new_ledger(today: dt.date) -> dict:
    return {"started": str(today), "cash": CAPITAL, "holdings": {}, "pending": None, "last_formation": None,
            "trades": [], "last_close": {}, "nifty_start": None, "last_report": None}


def by_symbol(acts: dict) -> dict:
    out = defaultdict(list)
    for (sym, d), (f, div) in acts.items():
        out[sym].append((d, f, div))
    return {k: sorted(v) for k, v in out.items()}


def position(h: dict, sym: str, upto: dt.date, acts_by_sym: dict) -> tuple[float, float]:
    """(shares now, dividends received) for a holding bought on h['since']."""
    qty, divs = h["qty"], 0.0
    since = dt.date.fromisoformat(h["since"])
    for d, f, div in acts_by_sym.get(sym, ()):
        if since < d <= upto:
            qty /= f
            divs += qty * div
    return qty, divs


def nav(ledger: dict, acts_by_sym: dict | None = None, upto: dt.date | None = None) -> float:
    acts_by_sym = acts_by_sym or {}
    upto = upto or dt.date.max
    total = ledger["cash"]
    for s, h in ledger["holdings"].items():
        qty, divs = position(h, s, upto, acts_by_sym)
        total += qty * ledger["last_close"].get(s, h["cost"]) + divs
    return total


def fill(ledger: dict, bhavs: dict, acts_by_sym: dict) -> list[str]:
    """Fill pending orders at the latest cached close, once it is after the formation day."""
    p = ledger["pending"]
    day = max(bhavs)
    if not p or day <= dt.date.fromisoformat(p["after"]):
        return []
    closes = {sym: row[0] for sym, row in bhavs[day].items()}
    lines = []
    for sym in p["sell"]:
        h = ledger["holdings"].get(sym)
        if not h or sym not in closes:
            continue  # suspended or delisted: retried at the next formation
        qty, divs = position(h, sym, day, acts_by_sym)
        value = qty * closes[sym]
        cost = value * _rates(value)[1] + DP
        ledger["cash"] += value + divs - cost
        del ledger["holdings"][sym]
        pnl = value + divs - cost - h["qty"] * h["cost"]
        ledger["trades"].append({"day": str(day), "side": "SELL", "sym": sym, "qty": round(qty, 4), "price": closes[sym],
                                 "cost": round(cost, 2), "pnl": round(pnl, 2)})
        lines.append(f"SELL {sym} {qty:.0f} @ {closes[sym]:.2f}  P&L Rs.{pnl:+,.0f}")
    buys = [s for s in p["buy"] if s in closes]
    budget = ledger["cash"] / len(buys) if buys else 0.0
    for sym in buys:
        px = closes[sym]
        qty = math.floor(budget / (px * (1 + _rates(budget)[0])))
        if qty <= 0:
            continue
        value = qty * px
        cost = value * _rates(value)[0]
        ledger["cash"] -= value + cost
        ledger["holdings"][sym] = {"qty": float(qty), "cost": px, "since": str(day)}
        ledger["last_close"][sym] = px
        ledger["trades"].append({"day": str(day), "side": "BUY", "sym": sym, "qty": qty, "price": px, "cost": round(cost, 2)})
        lines.append(f"BUY  {sym} {qty} @ {px:.2f}")
    ledger["pending"] = None
    return [f"Filled at the {day} close:"] + lines


def form(ledger: dict, bhavs: dict, acts: dict, today: dt.date) -> list[str] | None:
    """Form the new month's 30 after the previous month's last trading day; the very first
    formation uses the latest data and waits for the next close."""
    first = ledger["last_formation"] is None
    if first:
        f_day = max(bhavs)
    else:
        past = [d for d in bhavs if d < today.replace(day=1)]
        if not past or dt.date.fromisoformat(ledger["last_formation"]) >= past[-1]:
            return None
        f_day = past[-1]
    days, R, V, etf = series(bhavs, acts, f_day)
    picks = select(days, R, V, etf, bhavs[f_day])
    held = set(ledger["holdings"])
    sell = sorted(held - set(picks))
    buy = [s for s in picks if s not in held]
    ledger["pending"] = {"formed": str(f_day), "after": str(max(bhavs) if first else f_day), "buy": buy, "sell": sell, "picks": picks}
    ledger["last_formation"] = str(f_day)
    return [f"Momentum paper book: new picks from data to {f_day} (momentum + low volatility, top 30 of the 200 most traded).",
            f"Sell {len(sell)}: {', '.join(sell) or '-'}",
            f"Buy {len(buy)}: {', '.join(buy) or '-'}",
            f"Holding {len(picks)}: {', '.join(picks)}",
            "Orders fill at the next trading day's close (paper only)."]


def report(ledger: dict, src: Source, latest: dt.date, acts_by_sym: dict) -> list[str]:
    value = nav(ledger, acts_by_sym, latest)
    lines = [f"Momentum paper book, {latest}: NAV Rs.{value:,.0f} ({value / CAPITAL - 1:+.1%} since {ledger['started']}), "
             f"cash Rs.{ledger['cash']:,.0f}, {len(ledger['holdings'])} stocks."]
    if ledger.get("nifty_start"):
        now = src.nifty(latest)
        if now:
            lines.append(f"NIFTY 50 over the same period: {now / ledger['nifty_start'] - 1:+.1%}.")
    best = []
    for s, h in ledger["holdings"].items():
        qty, divs = position(h, s, latest, acts_by_sym)
        best.append(((qty * ledger["last_close"].get(s, h["cost"]) + divs) / (h["qty"] * h["cost"]) - 1, s))
    best.sort(reverse=True)
    if best:
        lines.append("Best: " + ", ".join(f"{s} {r:+.0%}" for r, s in best[:3]) + " | Worst: " + ", ".join(f"{s} {r:+.0%}" for r, s in best[-3:]))
    return lines


def load_ledger(today: dt.date) -> dict:
    return json.loads(LEDGER_PATH.read_text()) if LEDGER_PATH.exists() else new_ledger(today)


def save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    tmp.replace(LEDGER_PATH)


def run(src: Source, today: dt.date, send=technical_notifier.notify) -> dict:
    ledger = load_ledger(today)
    bhavs = sync(src, today)
    if len(bhavs) < 260:
        raise RuntimeError(f"only {len(bhavs)} trading days of bhavcopy available - need 260")
    acts = parse_actions(src.actions_range(min(bhavs), today))
    acts_by_sym = by_symbol(acts)
    latest = max(bhavs)
    if ledger["nifty_start"] is None:
        ledger["nifty_start"] = src.nifty(latest)
    closes = {sym: row[0] for sym, row in bhavs[latest].items()}
    for sym in ledger["holdings"]:
        if sym in closes:
            ledger["last_close"][sym] = float(closes[sym])
    msgs = []
    for _ in range(2):  # fill what is pending, then form; a late run can fill the new orders at once
        filled = fill(ledger, bhavs, acts_by_sym)
        if filled:
            msgs.append("\n".join(filled + [f"NAV Rs.{nav(ledger, acts_by_sym, latest):,.0f}, cash Rs.{ledger['cash']:,.0f}."]))
        formed = form(ledger, bhavs, acts, today)
        if not formed:
            break
        msgs.append("\n".join(formed))
    if today.weekday() == 0 and ledger.get("last_report") != str(today) and ledger["holdings"]:
        msgs.append("\n".join(report(ledger, src, latest, acts_by_sym)))
        ledger["last_report"] = str(today)
    save_ledger(ledger)
    for m in msgs:
        send("[PAPER] " + m)
    return ledger


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "status"))
    a = ap.parse_args()
    today = now_ist().date()
    if a.cmd == "status":
        ledger = load_ledger(today)
        print(json.dumps({k: ledger[k] for k in ("started", "cash", "last_formation", "pending")}, indent=1, default=str))
        print(f"NAV Rs.{nav(ledger):,.0f}, {len(ledger['holdings'])} holdings")
        return 0
    try:
        run(Source(), today)
    except Exception as e:  # noqa: BLE001 - one alert, then fail the unit so systemd shows it
        log.exception("momentum paper run failed")
        technical_notifier.notify_error(f"[PAPER] momentum book run failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
