import datetime as dt
import importlib.util
from pathlib import Path

from trading_bot import research_cli
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.research.metrics import Metrics
from trading_bot.engine.research.review import (
    counterfactual_summary,
    counterfactuals,
    daily_review,
    promote,
    promotion_record,
    rejected_analysis,
    render_rejection,
    component_attribution,
    render_attribution_row,
)
from trading_bot.engine.research.validation import GateResult
from trading_bot.timeutil import IST

_spec = importlib.util.spec_from_file_location("val_t", Path(__file__).resolve().parent / "test_engine_validation.py")
val_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(val_t)


def _signals():
    ts = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)
    out = [{"signal_id": "v1", "status": "valid", "stage": "SNAPSHOT", "reason_code": None, "underlying": "NIFTY", "direction": "up",
            "ts": ts, "snapshot": {"option_entry": 150.0}, "explanation": {"Strategy": "TREND_PULLBACK v0.1 (with trend)"}}]
    for i, (stage, code, status) in enumerate([("RISK_ENGINE", "one_lot_exceeds_max_risk", "risk_rejected"),
                                                ("RISK_ENGINE", "one_lot_exceeds_max_risk", "risk_rejected"),
                                                ("OPTION_SELECTION", "no_acceptable_option", "option_rejected"),
                                                ("NO_CHASE", "insufficient_remaining_move", "rejected")]):
        out.append({"signal_id": f"r{i}", "status": status, "stage": stage, "reason_code": code, "underlying": "NIFTY" if i % 2 else "SENSEX",
                    "direction": "up" if i < 3 else "down", "ts": ts + dt.timedelta(minutes=5 * (i + 1)),
                    "snapshot": {"spot": 25000.0, "atr": 20.0, "direction": "up" if i < 3 else "down"},
                    "explanation": {"Strategy": "PDH_PDL_TRAP v0.1 (counter-trend): x" if i == 3 else "ORB v0.1 (with trend)"}})
    return out


def test_daily_review_reports_best_worst_quality_and_rejections():
    rows = val_t._rows(40, edge=60.0)
    rows[0]["exit_reason"] = "STOP_LOSS"; rows[0]["details"]["entry_price"] = 150.0; rows[0]["details"]["initial_sl"] = 147.0; rows[0]["mfe"] = 400.0
    rev = daily_review(rows, _signals(), val_t._executions(), 100000.0, "2026-09")
    assert rev.overall.trades == 40 and "strategy" in rev.best and "regime" in rev.worst
    assert rev.best["regime"][1] >= rev.worst["regime"][1]
    assert rev.sl_quality["stopped"] == 1 and rev.sl_quality["stopped_after_being_in_profit"] == 1
    assert rev.target_quality["target_hits"] == 39 and rev.costs["per_trade"] == 60.0
    assert rev.execution["fill_probability"] == 0.9 and rev.rejected["total"] == 4
    text = rev.render()
    assert text.startswith("REVIEW 2026-09") and "SL quality" in text and text.endswith("(spec section 71).")


def test_rejected_analysis_counts():
    ra = rejected_analysis(_signals())
    assert ra["total"] == 4 and ra["valid"] == 1
    assert ra["by_stage"] == {"NO_CHASE": 1, "OPTION_SELECTION": 1, "RISK_ENGINE": 2}
    assert list(ra["by_reason"])[0] == "RISK_ENGINE:one_lot_exceeds_max_risk" and ra["counter_trend"] == 1
    assert ra["by_status"] == {"option_rejected": 1, "rejected": 1, "risk_rejected": 2}
    assert len(ra["ledger"]) == 4 and ra["ledger"][3]["time"] == "10:20" and ra["ledger"][3]["next"] is None


def test_rejection_ledger_names_reason_family_verdicts_trend_and_what_happened_next():
    """The nightly review must say WHY each signal was rejected (stage, reason,
    every family's verdict, the trend it was judged against) and what the
    underlying did next - so a recurring reason is visible day after day."""
    ts = dt.datetime(2026, 9, 21, 10, 10, tzinfo=IST)
    sig = {"signal_id": "x1", "status": "rejected", "stage": "STRATEGY_ROUTING", "reason_code": "no_strategy_match",
           "underlying": "NIFTY", "direction": "down", "ts": ts, "strategy": None, "score": None, "explanation": {},
           "snapshot": {"spot": 23373.0, "atr": 18.0, "direction": "down", "detail": "no family accepted",
                        "routing": {"PDH_PDL_TRAP": "trap_without_confirmation", "TREND_PULLBACK": "counter_trend_not_allowed"},
                        "trend_scores": {"1d": -0.7, "30m": 0.6, "5m": -0.4, "1m": None}}}
    sig2 = dict(sig, signal_id="x2", stage="NO_CHASE", reason_code="insufficient_remaining_move", strategy="PDH_PDL_TRAP", score=40.0,
                ts=ts + dt.timedelta(minutes=5), snapshot=dict(sig["snapshot"], routing={}))
    falling = [{"ts": ts + dt.timedelta(minutes=i), "open": 23373 - 2 * i, "high": 23374 - 2 * i, "low": 23372 - 2 * i,
                "close": 23373 - 2 * i} for i in range(1, 60)]
    ra = rejected_analysis([sig, sig2], lambda u: falling)
    assert ra["by_family"] == {"PDH_PDL_TRAP:trap_without_confirmation": 1, "TREND_PULLBACK:counter_trend_not_allowed": 1}
    assert ra["by_reason"] == {"NO_CHASE:insufficient_remaining_move": 1, "STRATEGY_ROUTING:no_strategy_match": 1}
    row = ra["ledger"][0]
    assert row["trend_scores"] == {"1d": -0.7, "30m": 0.6, "5m": -0.4} and row["next"] == "target_first" and row["max_favourable"] > 0
    text = daily_review([], [sig, sig2], [], 50000.0, "d", candles_1m=lambda u: falling).render()
    assert "routing verdicts: PDH_PDL_TRAP:trap_without_confirmation=1" in text
    line = [l for l in text.splitlines() if l.strip().startswith("10:10 NIFTY down")][0]
    assert "STRATEGY_ROUTING:no_strategy_match [1d -0.7 30m +0.6 5m -0.4] PDH_PDL_TRAP=trap_without_confirmation" in line
    assert "-> target_first mf=+" in line
    line2 = [l for l in text.splitlines() if l.strip().startswith("10:15 NIFTY down")][0]
    assert "NO_CHASE:insufficient_remaining_move PDH_PDL_TRAP s=40" in line2 and "no family accepted" in line2
    assert "never P&L" in text


def test_rejection_ledger_falls_back_to_explanation_and_rebuilds_atr_for_pre_ledger_rows():
    """Rows journaled before the ledger fields existed (2026-09-21) carry only
    spot + the explanation text: trend scores come from its Trend line, the
    detail from its Strategy line and the ATR is rebuilt from 1m candles."""
    ts = dt.datetime(2026, 9, 21, 10, 10, tzinfo=IST)
    sig = {"signal_id": "old", "status": "rejected", "stage": "STRATEGY_ROUTING", "reason_code": "no_strategy_match",
           "underlying": "NIFTY", "direction": "down", "ts": ts, "strategy": None, "score": None,
           "snapshot": {"ts": ts.isoformat(), "spot": 23385.75, "direction": "down", "stage_reached": "STRATEGY_ROUTING"},
           "explanation": {"Trend": "1d=STRONG_BEAR(-0.71), 30m=COUNTER_TREND(0.53), 5m=BULL(0.49), 1m=WEAK_BEAR(-0.30)",
                           "Strategy": "family_hint=unclassified (strategy engines are M2)"}}
    bars = [{"ts": ts + dt.timedelta(minutes=i), "open": 23385.0 - 2 * i, "high": 23388.0 - 2 * i, "low": 23382.0 - 2 * i,
             "close": 23385.0 - 2 * i} for i in range(-80, 60)]  # ~3-pt 1m bars falling 2/min: 5m ATR ~ 13
    ra = rejected_analysis([sig], lambda u: bars)
    row = ra["ledger"][0]
    assert row["trend_scores"] == {"1d": -0.71, "30m": 0.53, "5m": 0.49} and row["detail"].startswith("family_hint=")
    assert row["next"] == "target_first" and row["max_favourable"] > 0
    line = render_rejection(row)
    assert "[1d -0.7 30m +0.5 5m +0.5]" in line and "family_hint=unclassified" in line and "-> target_first" in line


def test_counterfactuals_are_underlying_only_and_separate():
    ts0 = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)

    def candles(u):
        # NIFTY: runs up 40 over 20 bars (target +30 first); SENSEX: drops 30 first (stop -20 first)
        out = []
        for i in range(1, 30):
            t = ts0 + dt.timedelta(minutes=i)
            px = 25000 + 2 * i if u == "NIFTY" else 25000 - 2 * i
            out.append({"ts": t, "open": px, "high": px + 1, "low": px - 1, "close": px})
        return out

    cfs = counterfactuals(_signals(), candles)
    assert len(cfs) == 4 and all(c.signal_id.startswith("r") for c in cfs)  # valid signals excluded
    by = {c.signal_id: c for c in cfs}
    assert by["r1"].outcome == "target_first" and by["r1"].target_ref == 25030.0 and by["r1"].stop_ref == 24980.0
    assert by["r0"].outcome == "stop_first"  # SENSEX up signal, price falls
    assert by["r3"].direction == "down" and by["r3"].outcome == "stop_first"  # NIFTY down signal; price rises through the +20 stop
    summary = counterfactual_summary(cfs)
    assert summary["total"] == 4 and "never comparable" in summary["note"]
    assert by["r1"].as_dict()["ts"] == (ts0 + dt.timedelta(minutes=10)).isoformat()


def test_promotion_record_and_persist():
    dal = MemoryDAL()
    g = GateResult("Gate 1 - Technical reliability", True, [("x", True, "")])
    rec = promotion_record("TREND_PULLBACK", "0.1", backtest=Metrics(trades=10), walk_forward={"stable": True}, monte_carlo={"ruin_probability": 0.0},
                           paper=Metrics(trades=120), gates=[g], limitations=["model option chain in backtest"], rollback_version=None,
                           decided_by="human", decision="shadow_only")
    assert rec["decision"] == "shadow_only" and rec["gates"][0]["passed"] and rec["limitations"]
    rid = promote(dal, rec)
    assert rid == 1 and dal.strategy_versions()[0]["params"]["decision"] == "shadow_only"
    rec2 = dict(rec, decision="paper")
    assert promote(dal, rec2) == 1 and dal.strategy_versions()[0]["params"]["decision"] == "paper"


def test_cli_runs_against_memory_dal(monkeypatch, capsys):
    dal = MemoryDAL()
    import uuid
    rid = uuid.UUID(int=1)
    dal.insert_run(rid, "PAPER", dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST))
    dal.end_run(rid, dt.datetime(2026, 9, 16, 15, 35, tzinfo=IST))
    for i, r in enumerate(val_t._rows(30, edge=80.0)):
        dal.insert_trade_result(rid, f"s{i}", entry_ts=r["entry_ts"], exit_ts=r["exit_ts"], entry_price=150.0, exit_price=155.0,
                                quantity=75, gross_pnl=r["gross_pnl"], costs=r["costs"], net_pnl=r["net_pnl"], r_multiple=1.0,
                                exit_reason="TARGET_1", mae=-50.0, mfe=100.0, details=r["details"])
    monkeypatch.setattr(research_cli, "_open", lambda cfg: dal)
    monkeypatch.setenv("TECH_DATABASE_URL", "memory")
    assert research_cli.main(["metrics", "--from", "2026-08-01", "--to", "2026-09-30", "--segments"]) == 0
    out = capsys.readouterr().out
    assert "trades=30" in out and "[strategy]" in out
    assert research_cli.main(["review", "--from", "2026-08-01", "--to", "2026-09-30"]) == 0
    assert "REVIEW" in capsys.readouterr().out
    rc = research_cli.main(["gates", "--from", "2026-08-01", "--to", "2026-09-30"])
    out = capsys.readouterr().out
    assert rc == 1 and "NOT READY FOR LIVE" in out and "valid_opportunities" in out
    assert research_cli.main(["walkforward", "--from", "2026-08-01", "--to", "2026-09-30", "--window", "7"]) == 0
    assert research_cli.main(["montecarlo", "--from", "2026-08-01", "--to", "2026-09-30", "--runs", "50"]) == 0
    assert '"ruin_probability"' in capsys.readouterr().out


def test_cli_telegram_flag_posts_the_report(monkeypatch, capsys):
    from trading_bot import technical_notifier
    dal = MemoryDAL()
    posted = []
    monkeypatch.setattr(research_cli, "_open", lambda cfg: dal)
    monkeypatch.setattr(technical_notifier, "notify", lambda m, html=False: posted.append(m))
    monkeypatch.setenv("TECH_DATABASE_URL", "memory")
    assert research_cli.main(["review", "--from", "2026-09-18", "--to", "2026-09-18", "--telegram"]) == 0
    assert posted and posted[0].startswith("REVIEW 2026-09-18")
    assert "REVIEW" in capsys.readouterr().out  # still printed


def test_component_attribution_splits_outcomes_by_component_strength_and_penalty():
    """The indicator freeze's pruning tool: every component/penalty gets
    'strong vs weak' (or 'applied vs absent') outcomes from trades (net P&L
    joined by signal_id) and from rejected signals (ledger counterfactual)."""
    ts = dt.datetime(2026, 9, 22, 10, 0, tzinfo=IST)
    comps_strong = {"price_action": 18, "structure": 12, "key_location": 12, "mtf_alignment": 12, "volume": 8,
                    "momentum": 8, "volatility": 4, "liquidity_execution": 4, "option_quality": 4}
    comps_weak = {k: 1 for k in comps_strong}
    sigs = [
        {"signal_id": "t1", "status": "valid", "stage": "SNAPSHOT", "underlying": "NIFTY", "direction": "up", "ts": ts,
         "snapshot": {"extra": {"score_components": comps_strong, "penalties": {}}}, "explanation": {}},
        {"signal_id": "t2", "status": "valid", "stage": "SNAPSHOT", "underlying": "NIFTY", "direction": "up", "ts": ts,
         "snapshot": {"extra": {"score_components": comps_weak, "penalties": {"against_vwap": 5}}}, "explanation": {}},
        {"signal_id": "r1", "status": "rejected", "stage": "RISK_ENGINE", "reason_code": "one_lot_exceeds_max_risk",
         "underlying": "NIFTY", "direction": "up", "ts": ts, "explanation": {},
         "snapshot": {"spot": 25000.0, "atr": 20.0, "score_components": comps_strong, "penalties": {"extension": 10}}},
    ]
    trades = [{"signal_id": "t1", "net_pnl": 900.0}, {"signal_id": "t2", "net_pnl": -400.0}, {"signal_id": "zzz", "net_pnl": 1.0}]
    ledger = [{"signal_id": "r1", "next": "target_first"}, {"signal_id": "t1", "next": None}]
    at = component_attribution(trades, sigs, ledger)
    assert at["momentum"]["strong"] == {"trades": 1, "net": 900.0, "cf_n": 1, "cf_target_first": 1}
    assert at["momentum"]["weak"] == {"trades": 1, "net": -400.0, "cf_n": 0, "cf_target_first": 0}
    assert at["-against_vwap"]["applied"]["trades"] == 1 and at["-against_vwap"]["absent"]["trades"] == 1
    assert at["-extension"]["applied"]["cf_target_first"] == 1
    line = render_attribution_row("momentum", at["momentum"])
    assert line.startswith("momentum: strong: 1 trades +900/trade, cf 100% target-first (n=1) | weak: 1 trades -400/trade")
    text = daily_review(trades[:2], sigs, [], 50000.0, "d").render()
    assert "component attribution" in text and "-against_vwap: applied: 1 trades -400/trade" in text
