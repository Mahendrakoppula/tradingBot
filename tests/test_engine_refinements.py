import datetime as dt
import uuid

from trading_bot import research_cli
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.research.refinements import CANDIDATES, render, track
from trading_bot.timeutil import IST

T0 = dt.datetime(2026, 9, 21, 10, 10, tzinfo=IST)


def _sig(i, routing=None, stage="STRATEGY_ROUTING", reason="no_strategy_match", status="rejected", direction="down", u="NIFTY", minutes=5):
    return {"signal_id": f"s{i}", "status": status, "stage": stage, "reason_code": reason, "underlying": u, "direction": direction,
            "ts": T0 + dt.timedelta(minutes=minutes * i), "strategy": None,
            "snapshot": {"spot": 23373.0, "atr": 18.0, "direction": direction, "routing": routing or {}}}


def _candles_down_then_up(u):
    """20 bars falling 2/bar (a short's 1.5-ATR target is hit at bar 14), then a rally."""
    out = []
    for i in range(1, 60):
        t = T0 + dt.timedelta(minutes=i)
        px = 23373 - 2 * i if i <= 20 else 23333 + 4 * (i - 20)
        out.append({"ts": t, "open": px, "high": px + 1, "low": px - 1, "close": px})
    return out


def _rally(u):
    """Every short is stopped first."""
    return [{"ts": T0 + dt.timedelta(minutes=i), "open": 23373 + 3 * i, "high": 23375 + 3 * i, "low": 23372 + 3 * i,
             "close": 23373 + 3 * i} for i in range(1, 60)]


def test_r1_counts_trap_without_confirmation_and_counterfactuals():
    sigs = [_sig(i, routing={"PDH_PDL_TRAP": "trap_without_confirmation", "LIQUIDITY_SWEEP_BOS": "no_structure_break_after_sweep"}) for i in range(3)]
    sigs.append(_sig(9, routing={"PDH_PDL_TRAP": "no_pdh_pdl_sweep"}))  # not R1
    sigs.append(_sig(10, status="valid", routing={"PDH_PDL_TRAP": "trap_without_confirmation"}))  # valid signals never count
    st = {s.code: s for s in track(sigs, _candles_down_then_up)}
    r1 = st["R1"]
    assert r1.occurrences == 3 and r1.by_underlying == {"NIFTY": 3}
    assert sum(r1.counterfactual.values()) == 3 and r1.counterfactual.get("target_first") == 3
    assert r1.target_first_share == 1.0 and r1.avg_max_favourable > 0
    assert not r1.ready and r1.verdict.startswith("watching")
    assert "R1: 3 occurrence(s)" in render([r1])


def test_readiness_needs_both_count_and_favourable_counterfactuals():
    twelve = [_sig(i, routing={"PDH_PDL_TRAP": "trap_without_confirmation"}, minutes=0) for i in range(12)]  # same bar
    good = {s.code: s for s in track(twelve, _candles_down_then_up)}["R1"]
    assert good.occurrences == 12 and good.target_first_share == 1.0 and good.ready and "READY" in good.verdict
    bad = {s.code: s for s in track(twelve, _rally)}["R1"]
    assert bad.occurrences == 12 and bad.target_first_share == 0.0 and not bad.ready and "unfavourable" in bad.verdict


def test_r2_matches_evidence_bar_and_no_chase():
    sigs = [_sig(0, routing={"LIQUIDITY_SWEEP_BOS": "counter_trend_evidence_insufficient"}),
            _sig(1, stage="NO_CHASE", reason="insufficient_remaining_move"),
            _sig(2, stage="RANKING", reason="below_min_score")]
    st = {s.code: s for s in track(sigs, _candles_down_then_up)}
    assert st["R2"].occurrences == 2 and st["R1"].occurrences == 0


def test_candidates_are_documented_and_never_auto_apply():
    assert [c.code for c in CANDIDATES] == ["R1", "R2", "R3"] and all(c.note.startswith("docs/ROADMAP.md") for c in CANDIDATES)
    assert "never auto-applied" in render([])


def test_cli_refinements_command(monkeypatch, capsys):
    dal = MemoryDAL()
    rid = uuid.UUID(int=3)
    dal.insert_run(rid, "PAPER", T0)
    dal.insert_signal(rid, signal_id=uuid.UUID(int=7), setup_id=None, ts=T0, mode="PAPER", underlying="NIFTY", direction="down",
                      stage="STRATEGY_ROUTING", status="rejected", explanation={}, reason_code="no_strategy_match",
                      snapshot={"spot": 23373.0, "atr": 18.0, "direction": "down", "routing": {"PDH_PDL_TRAP": "trap_without_confirmation"}})
    monkeypatch.setattr(research_cli, "_open", lambda cfg: dal)
    monkeypatch.setenv("TECH_DATABASE_URL", "memory")
    assert research_cli.main(["refinements", "--from", "2026-09-21", "--to", "2026-09-21", "--json"]) == 0
    out = capsys.readouterr().out
    assert "R1: 1 occurrence(s)" in out and '"code": "R2"' in out


def test_paper_loop_journals_per_family_routing_verdicts():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("pl_t", Path(__file__).resolve().parent / "test_engine_paper_loop.py")
    pl_t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pl_t)
    loop, clock = pl_t._loop(execute=False, mode="SHADOW")
    ctx = pl_t.pipe_t._ctx(regime={"primary": "NO_TRADE", "recent_primaries": []})  # gate: nothing routes
    pl_t._trade_ready(loop, ctx)
    snap = loop.dal.signals[0]["snapshot"]
    assert snap["stage_reached"] == "STRATEGY_ROUTING" and "routing" in snap and "detail" in snap
    ctx2 = pl_t.pipe_t._ctx(price_action={"labels": [], "anatomy": {}, "sweep": None}, structure={"last_event": None, "recent_sweeps": []})
    ev = pl_t.StageEvent("setup-2", "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "x", 21, {})
    loop._on_stage_event(ctx2, ev, 2)
    routing = loop.dal.signals[1]["snapshot"]["routing"]
    assert routing and all(isinstance(v, str) for v in routing.values()) and "PDH_PDL_TRAP" in routing


def test_r3_matches_daily_only_veto():
    def sig(direction, s30, s1d, routing):
        r = _sig(0, routing=routing, direction=direction)
        r["snapshot"]["trend_scores"] = {"1d": s1d, "30m": s30, "5m": 0.5, "1m": 0.0}
        return r
    yes = sig("up", 0.6, -0.7, {"TREND_PULLBACK": "counter_trend_not_allowed"})
    no_30m = sig("up", 0.1, -0.7, {"TREND_PULLBACK": "counter_trend_not_allowed"})  # 30m did not agree
    both = sig("up", -0.6, -0.7, {"TREND_PULLBACK": "counter_trend_not_allowed"})  # 30m opposed too
    other = sig("up", 0.6, -0.7, {"TREND_PULLBACK": "regime_incompatible"})
    st = {s.code: s for s in track([yes, no_30m, both, other], _candles_down_then_up)}
    assert st["R3"].occurrences == 1
