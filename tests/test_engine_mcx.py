"""MCX crude-oil spike (docs/ROADMAP.md C1): the same engine on the MCX
session, shadow-only, own unit and database. Pins the session profile, the
future-as-price-reference instrument resolution, OPTFUT chains, the config
defaults, the cost profile, deploy/mcx.env's shadow-only invariants and an
end-to-end run_live on MCX hours."""
import datetime as dt
import importlib.util
import re
from pathlib import Path

import pytest

from trading_bot.costs import CostRates
from trading_bot.engine import clock
from trading_bot.engine.config import EngineConfig
from trading_bot.engine.instruments import is_commodity, near_month_future, resolve_instruments, ws_type
from trading_bot.options import OptionChain
from trading_bot.timeutil import IST

ROOT = Path(__file__).resolve().parent.parent
D = dt.date(2026, 9, 21)  # Monday; the CRUDEOIL September future expires today


@pytest.fixture(autouse=True)
def _nse_after_each_test():
    """The session profile is process-global: never leak MCX into other tests."""
    yield
    clock.configure_session("NSE")


def _t(h, m, s=0):
    return dt.datetime(D.year, D.month, D.day, h, m, s, tzinfo=IST)


# --- clock ------------------------------------------------------------------------------------

def test_mcx_session_profile_anchors_bars_at_0900_and_ends_2330():
    prof = clock.configure_session("MCX")
    assert prof.name == "MCX" and clock.session_open() == dt.time(9, 0) and clock.session_close() == dt.time(23, 30)
    assert clock.bar_start(_t(9, 0, 5), "30m") == _t(9, 0) and clock.bar_start(_t(9, 44), "30m") == _t(9, 30)
    assert clock.bar_start(_t(9, 14), "5m") == _t(9, 10)  # 09:14 is INSIDE the MCX session, not pre-open
    assert clock.next_boundary(_t(23, 7), "30m") == _t(23, 30) and clock.bar_start(_t(23, 45), "1m") == _t(23, 29)
    assert clock.next_boundary(_t(12, 0), "1d") == _t(23, 30)
    assert clock.in_session(dt.time(9, 5)) and clock.in_session(dt.time(22, 0)) and not clock.in_session(dt.time(23, 30))
    assert clock.session_phase(dt.time(8, 59)) == "PRE_MARKET" and clock.session_phase(dt.time(18, 30)) == "17:00-20:00"
    assert clock.session_phase(dt.time(23, 0)) == "20:00-23:30" and clock.session_phase(dt.time(23, 40)) == "POST_MARKET"


def test_mcx_us_winter_close_stretches_the_last_phase():
    prof = clock.configure_session("MCX", dt.time(23, 55))
    assert prof.close == dt.time(23, 55) and prof.phases[-1] == (dt.time(20, 0), dt.time(23, 55), "20:00-23:55")
    assert clock.session_phase(dt.time(23, 50)) == "20:00-23:55" and clock.next_boundary(_t(23, 40), "30m") == _t(23, 55)
    with pytest.raises(ValueError):
        clock.configure_session("MCX", dt.time(8, 0))
    with pytest.raises(ValueError):
        clock.configure_session("NYMEX")


def test_nse_is_the_default_and_is_restored():
    clock.configure_session("MCX")
    clock.configure_session("NSE")
    assert clock.session_open() == dt.time(9, 15) and clock.bar_start(_t(9, 14), "5m") == _t(9, 15)
    assert clock.SESSION_OPEN == dt.time(9, 15) and clock.SESSION_CLOSE == dt.time(15, 30)


# --- instruments + options ------------------------------------------------------------------

MCX_ROWS = [
    {"token": "565899", "symbol": "CRUDEOILM21SEP26FUT", "name": "CRUDEOILM", "instrumenttype": "FUTCOM", "exch_seg": "MCX",
     "expiry": "21SEP2026", "strike": "0.000000", "lotsize": "10", "tick_size": "100.000000"},
    {"token": "565900", "symbol": "CRUDEOILM19OCT26FUT", "name": "CRUDEOILM", "instrumenttype": "FUTCOM", "exch_seg": "MCX",
     "expiry": "19OCT2026", "strike": "0.000000", "lotsize": "10", "tick_size": "100.000000"},
    {"token": "565901", "symbol": "CRUDEOILM19NOV26FUT", "name": "CRUDEOILM", "instrumenttype": "FUTCOM", "exch_seg": "MCX",
     "expiry": "19NOV2026", "strike": "0.000000", "lotsize": "10", "tick_size": "100.000000"},
    {"token": "565000", "symbol": "CRUDEOILM", "name": "CRUDEOILM", "instrumenttype": "COMDTY", "exch_seg": "MCX", "expiry": ""},
    {"token": "580685", "symbol": "CRUDEOILM15OCT267550CE", "name": "CRUDEOILM", "instrumenttype": "OPTFUT", "exch_seg": "MCX",
     "expiry": "15OCT2026", "strike": "755000.000000", "lotsize": "10", "freeze_qty": "100", "tick_size": "5.000000"},
    {"token": "580686", "symbol": "CRUDEOILM15OCT267550PE", "name": "CRUDEOILM", "instrumenttype": "OPTFUT", "exch_seg": "MCX",
     "expiry": "15OCT2026", "strike": "755000.000000", "lotsize": "10", "freeze_qty": "100", "tick_size": "5.000000"},
    {"token": "580373", "symbol": "CRUDEOIL15OCT267750CE", "name": "CRUDEOIL", "instrumenttype": "OPTFUT", "exch_seg": "MCX",
     "expiry": "15OCT2026", "strike": "775000.000000", "lotsize": "100", "freeze_qty": "100", "tick_size": "10.000000"},
]


def test_commodity_price_reference_is_the_rolled_near_month_future():
    assert is_commodity("CRUDEOILM") and is_commodity("crudeoil") and not is_commodity("NIFTY")
    insts = resolve_instruments(MCX_ROWS, ("CRUDEOILM",), D, volume_proxy="self")
    # expiry day of the September contract: rolled to October (2 days' notice), no separate volume proxy
    assert [(i.underlying, i.exchange, i.token, i.role) for i in insts] == [("CRUDEOILM", "MCX", "565900", "spot")]
    assert near_month_future(MCX_ROWS, "CRUDEOILM", "MCX", dt.date(2026, 9, 1))["token"] == "565899"
    assert near_month_future(MCX_ROWS, "CRUDEOILM", "MCX", dt.date(2026, 9, 1), min_days_to_expiry=2)["token"] == "565899"
    assert near_month_future(MCX_ROWS, "CRUDEOILM", "MCX", dt.date(2026, 9, 20), min_days_to_expiry=2)["token"] == "565900"
    assert ws_type("MCX") == "mcx_fo"
    with pytest.raises(RuntimeError):
        resolve_instruments(MCX_ROWS, ("CRUDEOILM",), dt.date(2027, 1, 1))


def test_option_chain_reads_optfut_rows_on_mcx():
    chain = OptionChain(MCX_ROWS, "CRUDEOILM", "MCX")
    assert len(chain.contracts) == 2 and chain.expiries() == [dt.date(2026, 10, 15)]
    ce = chain.nearest_strike(dt.date(2026, 10, 15), "CE", 7540.0)
    assert ce.strike == 7550.0 and ce.lotsize == 10 and ce.exchange == "MCX" and ce.tradingsymbol.endswith("CE")
    assert OptionChain(MCX_ROWS, "CRUDEOIL", "MCX").contracts[0].lotsize == 100
    assert OptionChain(MCX_ROWS, "CRUDEOILM", "NFO").contracts == []


# --- config + costs -------------------------------------------------------------------------

def test_mcx_session_config_defaults(monkeypatch):
    for name in ("TECH_SESSION", "TECH_EOD_CUTOFF", "TECH_SESSION_END", "TECH_EOD_SUMMARY_TIME", "TECH_COST_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    nse = EngineConfig.from_env()
    assert nse.session == "NSE" and nse.cost_profile == "nfo" and nse.session_end == dt.time(15, 30)
    monkeypatch.setenv("TECH_SESSION", "mcx")
    mcx = EngineConfig.from_env()
    assert mcx.session == "MCX" and mcx.cost_profile == "mcx" and mcx.future_roll_days == 2
    assert (mcx.eod_cutoff, mcx.session_end, mcx.eod_summary_time) == (dt.time(23, 15), dt.time(23, 30), dt.time(23, 35))
    monkeypatch.setenv("TECH_SESSION_END", "23:55")
    assert EngineConfig.from_env().session_end == dt.time(23, 55)
    monkeypatch.setenv("TECH_SESSION", "NYMEX")
    with pytest.raises(RuntimeError):
        EngineConfig.from_env()


def test_mcx_cost_profile_swaps_ctt_and_exchange_charge_only():
    nfo, mcx = CostRates.for_profile("nfo"), CostRates.for_profile("mcx")
    assert nfo == CostRates() and mcx.stt_sell_pct == 0.05 and mcx.exchange_txn_pct == 0.0418
    assert (mcx.brokerage_per_order, mcx.sebi_fee_pct, mcx.stamp_duty_pct, mcx.gst_pct) == \
        (nfo.brokerage_per_order, nfo.sebi_fee_pct, nfo.stamp_duty_pct, nfo.gst_pct)
    assert mcx.option_cost(100.0, 110.0, 10) < nfo.option_cost(100.0, 110.0, 10)  # 0.05% CTT vs 0.1% STT on the sell leg
    with pytest.raises(ValueError):
        CostRates.for_profile("cds")


# --- deploy/mcx.env: shadow-only invariants, like test_config_bounds pins config.env ----------

def _env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def test_mcx_env_never_live_and_isolated_from_the_index_engine():
    mcx = _env(ROOT / "deploy" / "mcx.env")
    idx = _env(ROOT / "deploy" / "config.env")
    # SHADOW or PAPER only - LIVE on MCX needs its own promotion record (spec 83); dry-run stays pinned
    assert mcx["TECH_MODE"] in ("SHADOW", "PAPER") and mcx["TECH_DRY_RUN"] == "true" and mcx["TECH_LIVE_TRADING_ENABLED"] == "false"
    assert mcx["TECH_SESSION"] == "MCX" and mcx["TECH_COST_PROFILE"] == "mcx" and mcx["TECH_VOLUME_PROXY"] == "self"
    assert mcx["TECH_MCX_ENABLED"] in ("true", "false")
    assert all(is_commodity(u) for u in mcx["TECH_UNDERLYINGS"].split(","))
    assert mcx["TECH_DATABASE_URL"] != idx["TECH_DATABASE_URL"] and "tradingbot_mcx" in mcx["TECH_DATABASE_URL"]
    cutoff, end, summary = (dt.datetime.strptime(mcx[k], "%H:%M").time() for k in ("TECH_EOD_CUTOFF", "TECH_SESSION_END", "TECH_EOD_SUMMARY_TIME"))
    assert dt.time(9, 0) < cutoff < end <= dt.time(23, 55) and end < summary
    assert 1 <= int(mcx["TECH_OPTION_DTE_MAX"]) <= 45 and 5 <= int(mcx["TECH_STALE_TICK_SECONDS"]) <= 120
    unit = (ROOT / "deploy" / "trading-bot-mcx.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/opt/trading-bot/.env\nEnvironmentFile=/opt/trading-bot/deploy/mcx.env" in unit
    assert "MemoryMax=" in unit and "run_technical" in unit
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "deploy/mcx.env" in workflow and "trading-bot-mcx.service" in workflow and "TECH_MCX_ENABLED=true" in workflow


# --- end to end on MCX hours ------------------------------------------------------------------

def _load_wiring():
    spec = importlib.util.spec_from_file_location("wiring_t", Path(__file__).resolve().parent / "test_run_technical_wiring.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_live_on_the_mcx_session(monkeypatch):
    w = _load_wiring()
    rt = w.rt
    day = D
    clockbox = {"now": dt.datetime.combine(day, dt.time(8, 30), tzinfo=IST)}

    class McxRest(w.FakeRest):
        def get_candle_data(self, exchange, token, interval, fromdate, todate):
            assert exchange == "MCX" and token == "565900"
            a = dt.datetime.strptime(fromdate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            b = dt.datetime.strptime(todate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            step = {"ONE_MINUTE": 1, "FIVE_MINUTE": 5, "THIRTY_MINUTE": 30, "ONE_DAY": 1440}[interval]
            rows, t, k = [], a, 0
            while t < b:
                if t.weekday() < 5 and (interval == "ONE_DAY" or dt.time(9, 0) <= t.time() < dt.time(23, 30)):
                    px = 7500 + (k % 7) * 3
                    rows.append([t.isoformat(), px, px + 4, px - 4, px + 1, 50])
                t += dt.timedelta(minutes=step)
                k += 1
            return rows

    class McxLookup(w.FakeLookup):
        def load(self):
            self.instruments = list(MCX_ROWS)

    class McxSource(w.FakeSource):
        def __init__(self, stream, q):
            self.stream, self.started, self.closed, self.last = stream, False, False, None
            t = dt.datetime.combine(day, dt.time(9, 0), tzinfo=IST)
            self.ticks = [w._Tick("565900", 750000 + k * 10, int((t + dt.timedelta(seconds=30 * k)).timestamp() * 1000), volume=100 + k)
                          for k in range(60)]

        def next(self, timeout=1.0):
            if not self.ticks:
                clockbox["now"] = dt.datetime.combine(day, dt.time(23, 30, 10), tzinfo=IST)
                return None
            t = self.ticks.pop(0)
            clockbox["now"] = dt.datetime.fromtimestamp(t.exchange_timestamp / 1000, tz=IST)
            self.last = clockbox["now"]
            return t

        def health(self):
            from trading_bot.engine.quality import FeedHealth
            return FeedHealth(connected=True, last_tick_at=self.last or clockbox["now"], reconnects=0)

    notes: list[str] = []
    monkeypatch.setattr(rt, "Session", w.FakeSession)
    monkeypatch.setattr(rt, "RestClient", McxRest)
    monkeypatch.setattr(rt, "InstrumentLookup", McxLookup)
    monkeypatch.setattr(rt, "ResilientMarketStream", lambda *a, **k: object())
    monkeypatch.setattr(rt, "smartapi_stream_factory", lambda session: None)
    monkeypatch.setattr(rt, "LiveTickSource", McxSource)
    monkeypatch.setattr(rt, "RateLimiter", w._NoSleepLimiter)
    monkeypatch.setattr(rt, "now_ist", lambda: clockbox["now"])
    monkeypatch.setattr(rt, "today_ist", lambda: day)
    monkeypatch.setattr(rt.technical_notifier, "notify", lambda m, html=False: notes.append(m))
    monkeypatch.setattr(rt, "_git_sha", lambda: "mcx1234")
    monkeypatch.setattr(rt.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(rt, "broker_config", lambda cfg: type("B", (), {"dry_run": True, "scrip_master_url": "x"})())
    dals = []
    orig = rt._open_dal

    def _dal(cfg):
        d = orig(cfg)
        dals.append(d)
        return d

    monkeypatch.setattr(rt, "_open_dal", _dal)

    cfg = w._cfg(underlyings=("CRUDEOILM",), volume_proxy="self", session="MCX", cost_profile="mcx",
                 eod_cutoff=dt.time(23, 0), session_end=dt.time(23, 30), eod_summary_time=dt.time(23, 35))
    clock.configure_session(cfg.session, cfg.session_end)  # what main() does before run_live
    assert rt.run_live(cfg) == 0
    dal = dals[0]
    run = next(iter(dal.runs.values()))
    assert run["status"] == "completed" and run["mode"] == "SHADOW"
    # 30 minutes of ticks from 09:00 -> six 5m closes on the MCX anchor, journaled with MCX phase labels
    assert dal.context_count(run["run_id"]) == 6
    assert {s["session_phase"] for s in dal.context_snapshots} == {"09:00-12:00"}
    assert any(k[1] == "1m" and k[2].date() == day and k[2].time() < dt.time(9, 15) for k in dal.candles)  # bars before NSE open
    assert notes[0].startswith("SHADOW MODE | LIVE ORDER PLACEMENT DISABLED") and "session=MCX" in notes[0]
    assert notes[-1].startswith(f"EOD {day} | SHADOW | zero orders")
