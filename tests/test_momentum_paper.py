import datetime as dt

import math
import random

import pytest

from trading_bot import momentum_paper as M


def test_parse_bhav_both_layouts_and_etf_names():
    legacy = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,\nABC,EQ,1,1,1,10,10,9,5,50,1-JAN-2010,\nABC,BE,1,1,1,11,11,9,5,50,1-JAN-2010,\nNIFTYBEES,EQ,1,1,1,100,100,99,5,500,1-JAN-2010,\n"
    day = M.parse_bhav(legacy)
    assert day == {"ABC": (10.0, 9.0, 50.0, False), "NIFTYBEES": (100.0, 99.0, 500.0, True)}
    udiff = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,"
             "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal\n"
             "2026-11-13,2026-11-13,CM,NSE,STK,1,X,TCS,EQ,,,,,TATA CONSULTANCY,1,1,1,3000,3000,2990,,3000,,,10,30000\n"
             "2026-11-13,2026-11-13,CM,NSE,STK,2,Y,GOLD360,EQ,,,,,360ONEAMC - GOLD360,1,1,1,90,90,89,,90,,,10,900\n")
    day = M.parse_bhav(udiff)
    assert {k: v[3] for k, v in day.items()} == {"TCS": False, "GOLD360": True} and day["TCS"][:3] == (3000.0, 2990.0, 30000.0)


def test_parse_actions_split_bonus_dividends_and_duplicates():
    recs = [
        {"symbol": "TATASTEEL", "exDate": "28-Jul-2022", "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"},
        {"symbol": "RELIANCE", "exDate": "28-Oct-2024", "subject": "Bonus 1:1"},
        {"symbol": "RELIANCE", "exDate": "28-Oct-2024", "subject": "Bonus 1:1"},  # listed again in another daily file
        {"symbol": "ITC", "exDate": "01-Jul-2016", "subject": "Bonus 1 : 2"},
        {"symbol": "TCS", "exDate": "31-May-2018", "subject": "Dividend - Rs 29 Per Share"},
        {"symbol": "OLD", "exDate": "02-Jan-2008", "subject": "Agm/Dividend-50%", "faceVal": "2"},
    ]
    a = M.parse_actions(recs)
    assert a[("TATASTEEL", dt.date(2022, 7, 28))][0] == pytest.approx(0.1)
    assert a[("RELIANCE", dt.date(2024, 10, 28))] == (0.5, 0.0)
    assert a[("ITC", dt.date(2016, 7, 1))][0] == pytest.approx(2 / 3)
    assert a[("TCS", dt.date(2018, 5, 31))] == (1.0, 29.0)
    assert a[("OLD", dt.date(2008, 1, 2))] == (1.0, 1.0)


def _frame(rows):
    return {sym: (close, prev, value, etf) for sym, close, prev, value, etf in rows}


def test_panels_adjust_splits_and_drop_unexplained_crashes():
    d1, d2 = dt.date(2022, 7, 27), dt.date(2022, 7, 28)
    bhavs = {d1: _frame([("TATASTEEL", 959.4, 949.5, 1e9, False), ("BAD", 100, 100, 1e8, False)]),
             d2: _frame([("TATASTEEL", 100.35, 959.4, 1e9, False), ("BAD", 30, 100, 1e8, False)])}
    acts = {("TATASTEEL", d2): (0.1, 0.0)}
    days, R, V, etf = M.series(bhavs, acts, d2)
    assert R["TATASTEEL"][1] == pytest.approx(100.35 / 95.94 - 1)
    assert R["BAD"][1] is None  # -70% with no corporate action: treated as a data error


def _universe(days, n=40, seed=7):
    """n stocks with drifts from -40% to +80% a year, plus a liquid ETF that must never be picked."""
    rng = random.Random(seed)
    drift = [(-0.4 + 1.2 * i / (n - 1)) / 252 for i in range(n)]
    vol = [(0.30 - 0.15 * i / (n - 1)) / math.sqrt(252) for i in range(n)]  # higher drift also calmer: unambiguous ranking
    px = [500.0] * n
    etf_px = 1000.0
    out = {}
    for d in days:
        prev = px[:]
        px = [p * math.exp(drift[i] + vol[i] * rng.gauss(0, 1)) for i, p in enumerate(px)]
        rows = [(f"S{i:02d}", px[i], prev[i], 5e8 + i * 1e6, False) for i in range(n)]
        rows.append(("LIQUIDBEES", etf_px * 1.0002, etf_px, 9e9, True))
        etf_px *= 1.0002
        out[d] = _frame(rows)
    return out


def _weekdays(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_select_prefers_strong_calm_trends_and_skips_etfs():
    days = _weekdays(dt.date(2025, 9, 1), dt.date(2026, 10, 30))
    bhavs = _universe(days)
    dd, R, V, etf = M.series(bhavs, {}, days[-1])
    picks = M.select(dd, R, V, etf, bhavs[days[-1]])
    assert len(picks) == 30 and "LIQUIDBEES" not in picks
    assert {"S39", "S38", "S37"} <= set(picks) and "S00" not in picks


class FakeSource:
    def __init__(self, bhavs):
        self.bhavs = bhavs

    def bhav(self, d):
        return self.bhavs.get(d)

    def actions(self, d):
        return []

    def nifty(self, d):
        return 25000.0


def test_run_forms_then_fills_next_close_then_rotates_monthly(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "LEDGER_PATH", tmp_path / "ledger.json")
    src = FakeSource(_universe(_weekdays(dt.date(2025, 6, 2), dt.date(2026, 12, 15))))
    sent = []
    led = M.run(src, dt.date(2026, 11, 20), send=sent.append)  # first run: forms from the 19 Nov data, no fill yet
    assert led["pending"] and led["pending"]["after"] == "2026-11-19" and not led["holdings"]
    assert len(sent) == 1 and "new picks" in sent[0] and sent[0].startswith("[PAPER]")
    led = M.run(src, dt.date(2026, 11, 20), send=sent.append)  # same day again: nothing new
    assert len(sent) == 1 and not led["holdings"]
    led = M.run(src, dt.date(2026, 11, 23), send=sent.append)  # Monday: fills at the 20 Nov close + weekly report
    assert len(led["holdings"]) == 30 and led["pending"] is None and led["cash"] >= 0
    assert all(t["day"] == "2026-11-20" for t in led["trades"]) and len(led["trades"]) == 30
    spent = sum(t["qty"] * t["price"] + t["cost"] for t in led["trades"])
    assert led["cash"] + spent == pytest.approx(M.CAPITAL)
    assert any("Filled at the 2026-11-20 close" in m for m in sent) and any("NAV Rs." in m for m in sent)
    led = M.run(src, dt.date(2026, 12, 1), send=sent.append)  # new month: formation from the 30 Nov data
    assert led["last_formation"] == "2026-11-30" and led["pending"]["after"] == "2026-11-30"
    n_before = len(led["trades"])
    led = M.run(src, dt.date(2026, 12, 2), send=sent.append)  # fills at the 1 Dec close
    rotated = led["trades"][n_before:]
    assert all(t["day"] == "2026-12-01" for t in rotated)
    sells = [t for t in rotated if t["side"] == "SELL"]
    buys = [t for t in rotated if t["side"] == "BUY"]
    assert len(buys) == len(sells) and len(led["holdings"]) == 30


def test_position_applies_splits_after_purchase_only():
    h = {"qty": 10.0, "cost": 1000.0, "since": "2024-10-25"}
    acts = M.by_symbol({("RELIANCE", dt.date(2024, 10, 28)): (0.5, 0.0), ("RELIANCE", dt.date(2024, 10, 25)): (0.5, 0.0),
                        ("RELIANCE", dt.date(2025, 8, 14)): (1.0, 5.5)})
    qty, divs = M.position(h, "RELIANCE", dt.date(2025, 12, 31), acts)
    assert qty == 20.0 and divs == pytest.approx(20 * 5.5)  # the bonus on the buy day itself is already in the price
