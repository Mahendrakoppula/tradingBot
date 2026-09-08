import datetime as dt
import json

import trading_bot.option_chain_logger as ocl
from trading_bot.option_chain_logger import log_snapshot


class FakeInstruments:
    def __init__(self, rows):
        self.instruments = rows


class FakeRest:
    def __init__(self, ltp, quotes_by_token, greeks_by_underlying=None, pcr_rows=None):
        self._ltp = ltp
        self._quotes_by_token = quotes_by_token
        self._greeks_by_underlying = greeks_by_underlying or {}
        self._pcr_rows = pcr_rows if pcr_rows is not None else []
        self.quote_calls = []

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self._ltp)}

    def get_quote(self, mode, exchange_tokens):
        self.quote_calls.append(exchange_tokens)
        tokens = exchange_tokens["NFO"]
        fetched = [self._quotes_by_token[t] for t in tokens if t in self._quotes_by_token]
        return {"data": {"fetched": fetched, "unfetched": []}}

    def get_option_greeks(self, name, expirydate):
        return self._greeks_by_underlying.get(name, [])

    def get_pcr(self):
        return self._pcr_rows


EXPIRY = (dt.date.today() + dt.timedelta(days=7)).strftime("%d%b%Y").upper()


def _option_row(token, strike, option_type):
    return {
        "token": token,
        "symbol": f"NIFTY{EXPIRY}{int(strike)}{option_type}",
        "name": "NIFTY",
        "expiry": EXPIRY,
        "strike": str(strike * 100),
        "lotsize": "25",
        "freeze_qty": "1800",
        "instrumenttype": "OPTIDX",
        "exch_seg": "NFO",
    }


def _spot_row():
    return {
        "token": "99926000",
        "symbol": "NIFTY",
        "name": "NIFTY",
        "instrumenttype": "AMXIDX",
        "exch_seg": "NSE",
    }


def _quote(token, symbol, ltp, oi=1000, bid=None, ask=None):
    return {
        "symbolToken": token,
        "tradingSymbol": symbol,
        "ltp": ltp,
        "open": ltp,
        "high": ltp,
        "low": ltp,
        "close": ltp,
        "opnInterest": oi,
        "tradeVolume": 5000,
        "depth": {
            "buy": [{"price": bid, "quantity": 100}] if bid else [],
            "sell": [{"price": ask, "quantity": 100}] if ask else [],
        },
    }


def test_log_snapshot_writes_only_contracts_near_spot(tmp_path, monkeypatch):
    monkeypatch.setattr(ocl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ocl.time, "sleep", lambda s: None)

    rows = [
        _option_row("1", 25000, "CE"),  # within band of spot=25000
        _option_row("2", 25000, "PE"),
        _option_row("3", 40000, "CE"),  # far OTM - outside +-15% band, should be excluded
    ]
    instruments = FakeInstruments(rows + [_spot_row()])
    quotes = {
        "1": _quote("1", rows[0]["symbol"], ltp=120.5, bid=119.0, ask=121.0),
        "2": _quote("2", rows[1]["symbol"], ltp=118.0, bid=117.0, ask=119.0),
        "3": _quote("3", rows[2]["symbol"], ltp=5.0),
    }
    greeks = {"NIFTY": [
        {"strikePrice": "25000.000000", "optionType": "CE", "impliedVolatility": "12.5"},
        {"strikePrice": "25000.000000", "optionType": "PE", "impliedVolatility": "13.1"},
    ]}
    pcr_rows = [{"pcr": 0.91, "tradingSymbol": "NIFTY29SEP26FUT"}, {"pcr": 0.83, "tradingSymbol": "BANKNIFTY29SEP26FUT"}]
    rest = FakeRest(ltp=25000.0, quotes_by_token=quotes, greeks_by_underlying=greeks, pcr_rows=pcr_rows)

    today = dt.date.today()
    log_snapshot(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=today, strike_band_pct=0.15)

    out_file = tmp_path / f"NIFTY_{today.isoformat()}.jsonl"
    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["underlying"] == "NIFTY"
    assert record["spot"] == 25000.0
    assert record["pcr"] == 0.91
    strikes_logged = {c["strike"] for c in record["contracts"]}
    assert strikes_logged == {25000.0}  # the far-OTM 40000 strike must be excluded
    ce_row = next(c for c in record["contracts"] if c["option_type"] == "CE")
    assert ce_row["ltp"] == 120.5
    assert ce_row["best_bid"] == 119.0
    assert ce_row["best_ask"] == 121.0
    assert ce_row["iv"] == 12.5
    pe_row = next(c for c in record["contracts"] if c["option_type"] == "PE")
    assert pe_row["iv"] == 13.1


def test_log_snapshot_skips_underlying_with_no_expiry_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr(ocl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ocl.time, "sleep", lambda s: None)
    instruments = FakeInstruments([_spot_row()])  # no option contracts at all
    rest = FakeRest(ltp=25000.0, quotes_by_token={})

    log_snapshot(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=dt.date.today(), strike_band_pct=0.15)

    assert list(tmp_path.iterdir()) == []


def test_log_snapshot_never_raises_on_quote_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ocl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ocl.time, "sleep", lambda s: None)

    class BrokenRest(FakeRest):
        def get_quote(self, mode, exchange_tokens):
            raise RuntimeError("boom")

    rows = [_option_row("1", 25000, "CE")]
    instruments = FakeInstruments(rows + [_spot_row()])
    rest = BrokenRest(ltp=25000.0, quotes_by_token={})

    # Must not raise - a logging failure can never be allowed to interrupt
    # the live strategy loop that calls this alongside real trading logic.
    log_snapshot(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=dt.date.today(), strike_band_pct=0.15)


def test_log_snapshot_never_raises_on_iv_or_pcr_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ocl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ocl.time, "sleep", lambda s: None)

    class BrokenGreeksAndPcrRest(FakeRest):
        def get_option_greeks(self, name, expirydate):
            raise RuntimeError("boom")

        def get_pcr(self):
            raise RuntimeError("boom")

    rows = [_option_row("1", 25000, "CE")]
    instruments = FakeInstruments(rows + [_spot_row()])
    quotes = {"1": _quote("1", rows[0]["symbol"], ltp=120.5)}
    rest = BrokenGreeksAndPcrRest(ltp=25000.0, quotes_by_token=quotes)

    today = dt.date.today()
    log_snapshot(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=today, strike_band_pct=0.15)

    record = json.loads((tmp_path / f"NIFTY_{today.isoformat()}.jsonl").read_text(encoding="utf-8").strip())
    assert record["pcr"] is None
    assert record["contracts"][0]["iv"] is None  # IV missing, not the whole snapshot lost
    assert record["contracts"][0]["ltp"] == 120.5  # quote data still made it through


def test_pcr_matching_does_not_confuse_banknifty_with_nifty(tmp_path, monkeypatch):
    monkeypatch.setattr(ocl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ocl.time, "sleep", lambda s: None)

    result = ocl._fetch_pcr_by_underlying(FakeRest(
        ltp=25000.0, quotes_by_token={},
        pcr_rows=[{"pcr": 0.91, "tradingSymbol": "NIFTY29SEP26FUT"}, {"pcr": 0.83, "tradingSymbol": "BANKNIFTY29SEP26FUT"}],
    ))
    assert result["NIFTY"] == 0.91
    assert result["BANKNIFTY"] == 0.83
