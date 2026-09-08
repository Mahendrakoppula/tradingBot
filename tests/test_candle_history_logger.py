import datetime as dt
import json

import trading_bot.candle_history_logger as chl
from trading_bot.candle_history_logger import (
    fetch_candles_with_oi,
    log_candles,
    maybe_log_candles,
    select_tracked_contracts,
)
from trading_bot.options import OptionChain, OptionContract


class FakeInstruments:
    def __init__(self, rows):
        self.instruments = rows


class FakeRest:
    def __init__(self, ltp, candles_by_token=None, oi_by_token=None, raise_on_oi=False):
        self._ltp = ltp
        self._candles_by_token = candles_by_token or {}
        self._oi_by_token = oi_by_token or {}
        self.raise_on_oi = raise_on_oi
        self.candle_calls = []
        self.oi_calls = []

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self._ltp)}

    def get_candle_data(self, exchange, symboltoken, interval, fromdate, todate):
        self.candle_calls.append((symboltoken, interval))
        return self._candles_by_token.get(symboltoken, [])

    def get_oi_data(self, exchange, symboltoken, interval, fromdate, todate):
        self.oi_calls.append((symboltoken, interval))
        if self.raise_on_oi:
            raise RuntimeError("boom")
        return self._oi_by_token.get(symboltoken, [])


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
    return {"token": "99926000", "symbol": "NIFTY", "name": "NIFTY", "instrumenttype": "AMXIDX", "exch_seg": "NSE"}


def test_select_tracked_contracts_picks_band_around_atm():
    rows = [_option_row(str(i), strike, ot) for i, strike in enumerate([24800, 24900, 25000, 25100, 25200, 25300], start=1) for ot in ("CE", "PE")]
    chain = OptionChain(rows, "NIFTY", exchange="NFO")
    expiry = dt.datetime.strptime(EXPIRY, "%d%b%Y").date()

    contracts = select_tracked_contracts(chain, expiry, spot=25000.0, strikes_each_side=1)
    strikes = {c.strike for c in contracts}
    assert strikes == {24900.0, 25000.0, 25100.0}  # ATM +-1, both CE and PE
    assert len(contracts) == 6  # 3 strikes x 2 option types


def test_fetch_candles_with_oi_merges_by_timestamp():
    contract = OptionContract(token="1", tradingsymbol="NIFTY25000CE", name="NIFTY", expiry=dt.date.today(),
                               strike=25000, option_type="CE", lotsize=25, freeze_qty=1800, exchange="NFO")
    candles = [["2026-09-08T09:15:00+05:30", 100.0, 105.0, 98.0, 102.0, 5000]]
    oi = [{"time": "2026-09-08T09:15:00+05:30", "oi": 12345}]
    rest = FakeRest(ltp=25000.0, candles_by_token={"1": candles}, oi_by_token={"1": oi})

    rows = fetch_candles_with_oi(rest, contract, "ONE_MINUTE", "2026-09-08 09:15", "2026-09-08 09:20")
    assert len(rows) == 1
    assert rows[0]["open"] == 100.0
    assert rows[0]["volume"] == 5000
    assert rows[0]["oi"] == 12345


def test_fetch_candles_with_oi_survives_oi_failure():
    contract = OptionContract(token="1", tradingsymbol="NIFTY25000CE", name="NIFTY", expiry=dt.date.today(),
                               strike=25000, option_type="CE", lotsize=25, freeze_qty=1800, exchange="NFO")
    candles = [["2026-09-08T09:15:00+05:30", 100.0, 105.0, 98.0, 102.0, 5000]]
    rest = FakeRest(ltp=25000.0, candles_by_token={"1": candles}, raise_on_oi=True)

    rows = fetch_candles_with_oi(rest, contract, "ONE_MINUTE", "2026-09-08 09:15", "2026-09-08 09:20")
    assert len(rows) == 1
    assert rows[0]["oi"] is None  # OI failure doesn't lose the candle data


def test_log_candles_writes_expected_json(tmp_path, monkeypatch):
    monkeypatch.setattr(chl, "LOG_DIR", tmp_path)
    contract = OptionContract(token="1", tradingsymbol="NIFTY25000CE", name="NIFTY", expiry=dt.date.today(),
                               strike=25000, option_type="CE", lotsize=25, freeze_qty=1800, exchange="NFO")
    rows = [{"time": "2026-09-08T09:15:00+05:30", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 5000, "oi": 12345}]

    log_candles("NIFTY", contract, "ONE_MINUTE", dt.date.today(), rows)

    out_file = tmp_path / f"NIFTY_NIFTY25000CE_ONE_MINUTE_{dt.date.today().isoformat()}.json"
    assert out_file.exists()
    record = json.loads(out_file.read_text(encoding="utf-8"))
    assert record["tradingsymbol"] == "NIFTY25000CE"
    assert record["interval"] == "ONE_MINUTE"
    assert record["candles"] == rows


def test_maybe_log_candles_only_processes_one_due_interval_per_call(tmp_path, monkeypatch):
    monkeypatch.setattr(chl, "LOG_DIR", tmp_path)
    rows = [_option_row("1", 25000, "CE"), _option_row("2", 25000, "PE")]
    instruments = FakeInstruments(rows + [_spot_row()])
    rest = FakeRest(ltp=25000.0, candles_by_token={"1": [], "2": []})

    tracked_cache: dict = {}
    last_pull_at: dict = {}
    today = dt.date.today()

    maybe_log_candles(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=today,
                       strikes_each_side=0, tracked_contracts_cache=tracked_cache, last_pull_at=last_pull_at)

    # Exactly one interval's worth of calls happened (2 contracts x 2 calls
    # each = 4), not all 5 intervals stacked into one tick.
    assert len(rest.candle_calls) == 2
    assert len(set(interval for _, interval in rest.candle_calls)) == 1
    assert len(last_pull_at) == 1


def test_maybe_log_candles_skips_when_nothing_due(tmp_path, monkeypatch):
    monkeypatch.setattr(chl, "LOG_DIR", tmp_path)
    instruments = FakeInstruments([_spot_row()])
    rest = FakeRest(ltp=25000.0)

    last_pull_at = {name: __import__("time").monotonic() for name in chl.INTERVAL_CONFIG}
    maybe_log_candles(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=dt.date.today(),
                       strikes_each_side=0, tracked_contracts_cache={}, last_pull_at=last_pull_at)

    assert rest.candle_calls == []


def test_maybe_log_candles_never_raises_on_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(chl, "LOG_DIR", tmp_path)

    class BrokenRest(FakeRest):
        def get_candle_data(self, *a, **kw):
            raise RuntimeError("boom")

    rows = [_option_row("1", 25000, "CE")]
    instruments = FakeInstruments(rows + [_spot_row()])
    rest = BrokenRest(ltp=25000.0)

    maybe_log_candles(rest, instruments, ("NIFTY",), dte_min=0, dte_max=45, today=dt.date.today(),
                       strikes_each_side=0, tracked_contracts_cache={}, last_pull_at={})
