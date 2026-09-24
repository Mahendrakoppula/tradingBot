"""Deterministic end-of-day summary of the DAILY bot's paper trades.

The nightly Claude review agent (deploy/daily_review_prompt.md) reads this
bot's journal and reasons about it. It has been down since 2026-09-14 - the
Anthropic account ran out of credit - and nobody noticed for seven trading
days, so twenty trades went unreviewed. This module is the safety net: no
model, no API key, no judgement, just the numbers, posted with the engine's
15:45 review. It never proposes a change; the agent (or a human) still does
the thinking.

Counts only THIS bot's trades. `.state/trade_log.jsonl` also holds records
from the second bot decommissioned on 2026-09-16, tagged `technical_*`,
which kept its own capital ledger - see state.own_realized_pnl().
"""
import datetime as dt
import json
from collections import defaultdict

from trading_bot import state as state_mod


def _rows() -> list[dict]:
    if not state_mod.TRADE_LOG_PATH.exists():
        return []
    out = []
    for line in state_mod.TRADE_LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("strategy") in state_mod.OWN_STRATEGY_TAGS:
            out.append(rec)
    return out


def _pnl(r: dict) -> float:
    return float(r.get("realized_pnl") or 0.0)


def _hold_minutes(r: dict) -> float | None:
    try:
        a = dt.datetime.fromisoformat(r["entered_at"])
        b = dt.datetime.fromisoformat(r["closed_at"])
    except (KeyError, ValueError):
        return None
    return (b - a).total_seconds() / 60.0


def summarise(today: dt.date, rows: list[dict] | None = None, ledger: dict | None = None) -> str:
    """One Telegram-sized block: today's trades, the running record, and the
    concentration check that says how much of the total rests on a couple of
    trades (20 trades is a small sample and the summary should say so)."""
    rows = _rows() if rows is None else rows
    lines = [f"DAILY BOT {today:%Y-%m-%d}"]
    if not rows:
        return lines[0] + "\nno trades logged yet"

    todays = [r for r in rows if str(r.get("closed_at", "")).startswith(today.isoformat())]
    if todays:
        day_pnl = sum(_pnl(r) for r in todays)
        lines.append(f"today: {len(todays)} trade(s), net {day_pnl:+,.0f}")
        for r in todays:
            held = _hold_minutes(r)
            lines.append(f"  {r.get('underlying','?')} {r.get('option_type','?')} "
                         f"x{r.get('qty_lots','?')} {('%.0fm' % held) if held is not None else '?'} "
                         f"{_pnl(r):+,.0f} ({r.get('reason','?')})")
    else:
        lines.append("today: no trades")

    p = [_pnl(r) for r in rows]
    wins = [x for x in p if x > 0]
    losses = [x for x in p if x <= 0]
    total = sum(p)
    lines.append(f"all time: {len(p)} trades, net {total:+,.0f}, win {len(wins)}/{len(p)}"
                 f" = {len(wins) / len(p):.0%}")
    if wins and losses:
        lines.append(f"  avg win {sum(wins) / len(wins):+,.0f} | avg loss {sum(losses) / len(losses):+,.0f}"
                     f" | best {max(p):+,.0f} | worst {min(p):+,.0f}")

    # concentration: how much of the result rests on the best two trades
    if len(p) >= 4 and total > 0:
        top2 = sum(sorted(p, reverse=True)[:2])
        rest = total - top2
        lines.append(f"  top 2 trades {top2:+,.0f} of {total:+,.0f}; the other {len(p) - 2} sum {rest:+,.0f}")

    by_reason = defaultdict(list)
    for r in rows:
        by_reason[str(r.get("reason", "?"))].append(_pnl(r))
    lines.append("by exit: " + ", ".join(
        f"{k}={sum(v):+,.0f}(n{len(v)})" for k, v in sorted(by_reason.items(), key=lambda kv: -sum(kv[1]))))

    if ledger:
        delta = round(float(ledger.get("current_capital", 0.0))
                      - float(ledger.get("starting_capital", 0.0)) - total, 2)
        state = "reconciles" if abs(delta) < 1.0 else f"DOES NOT RECONCILE (delta {delta:+,.2f})"
        lines.append(f"ledger: {float(ledger.get('current_capital', 0.0)):,.0f} "
                     f"from {float(ledger.get('starting_capital', 0.0)):,.0f} - {state}")
    lines.append("Baseline only - not tuned on this sample (spec section 71).")
    return "\n".join(lines)


def main() -> int:
    from trading_bot import notifier
    from trading_bot.config import Config
    from trading_bot.timeutil import today_ist

    try:
        ledger = json.loads(state_mod.CAPITAL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ledger = None
    text = summarise(today_ist(), ledger=ledger)
    print(text)
    try:
        Config.from_env()  # only to surface a misconfigured env the same way the bot would
    except Exception:  # noqa: BLE001 - the summary is worth posting even then
        pass
    notifier.notify(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
