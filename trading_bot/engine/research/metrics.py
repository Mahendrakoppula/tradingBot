"""Performance metrics (spec §77) and fingerprint statistics (§63) over
closed trades. Input rows are the journal's `trade_results` shape (the
`details` jsonb carries the full §62 TradeResult, which is where the
segmentation keys live). Pure functions; stdlib only.

All ratios use NET P&L (§33 "always report gross, costs, net"; §92 #23).
Sharpe/Sortino are computed on DAILY net P&L in rupees divided by capital
(not annualised - there is no meaningful risk-free leg intraday and the
sample sizes are small); the annualised figure is offered separately and
should be read with the trade count next to it.
"""
import datetime as dt
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

SEGMENT_KEYS = ("strategy", "underlying", "option_type", "trend", "regime", "time_of_day", "expiry", "strike", "fingerprint")


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0  # net per trade
    profit_factor: float | None = None  # gross wins / gross losses (net basis)
    max_drawdown: float = 0.0  # rupees, peak-to-trough on cumulative net
    max_drawdown_pct: float = 0.0  # of capital
    recovery_factor: float | None = None  # net / max_dd
    calmar: float | None = None  # net/capital / max_dd_pct
    sharpe_daily: float | None = None
    sortino_daily: float | None = None
    sharpe_annualised: float | None = None
    max_win_streak: int = 0
    max_loss_streak: int = 0
    avg_holding_minutes: float = 0.0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    avg_r: float | None = None
    days: int = 0
    exit_reasons: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _ts(x) -> dt.datetime | None:
    if isinstance(x, dt.datetime):
        return x
    if isinstance(x, str):
        try:
            return dt.datetime.fromisoformat(x)
        except ValueError:
            return None
    return None


def _detail(row: dict, key: str, default=None):
    d = row.get("details") or {}
    return d.get(key, row.get(key, default))


def compute(rows: list[dict], capital: float) -> Metrics:
    m = Metrics()
    if not rows:
        return m
    rows = sorted(rows, key=lambda r: (_ts(r.get("exit_ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)))
    nets = [_f(r.get("net_pnl")) for r in rows]
    m.trades = len(rows)
    m.gross_pnl = round(sum(_f(r.get("gross_pnl")) for r in rows), 2)
    m.costs = round(sum(_f(r.get("costs")) for r in rows), 2)
    m.net_pnl = round(sum(nets), 2)
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    m.wins, m.losses = len(wins), len(losses)
    m.win_rate = round(m.wins / m.trades, 4)
    m.avg_win = round(statistics.mean(wins), 2) if wins else 0.0
    m.avg_loss = round(statistics.mean(losses), 2) if losses else 0.0
    m.expectancy = round(m.net_pnl / m.trades, 2)
    gl = -sum(losses)
    m.profit_factor = round(sum(wins) / gl, 3) if gl > 0 else None
    # drawdown on the cumulative net curve
    peak = cum = 0.0
    dd = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    m.max_drawdown = round(dd, 2)
    m.max_drawdown_pct = round(dd / capital, 4) if capital else 0.0
    m.recovery_factor = round(m.net_pnl / dd, 3) if dd > 0 else None
    m.calmar = round((m.net_pnl / capital) / m.max_drawdown_pct, 3) if (capital and m.max_drawdown_pct > 0) else None
    # streaks
    cur_w = cur_l = 0
    for n in nets:
        if n > 0:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        m.max_win_streak = max(m.max_win_streak, cur_w)
        m.max_loss_streak = max(m.max_loss_streak, cur_l)
    # daily returns for Sharpe/Sortino
    by_day: dict[dt.date, float] = defaultdict(float)
    for r, n in zip(rows, nets):
        t = _ts(r.get("exit_ts"))
        by_day[t.date() if t else dt.date.min] += n
    m.days = len(by_day)
    if capital and m.days >= 2:
        rets = [v / capital for v in by_day.values()]
        mu = statistics.mean(rets)
        sd = statistics.pstdev(rets)
        m.sharpe_daily = round(mu / sd, 3) if sd > 0 else None
        m.sharpe_annualised = round(m.sharpe_daily * math.sqrt(252), 3) if m.sharpe_daily is not None else None
        downside = [min(0.0, x) for x in rets]
        dsd = math.sqrt(sum(x * x for x in downside) / len(rets))
        m.sortino_daily = round(mu / dsd, 3) if dsd > 0 else None
    m.avg_holding_minutes = round(statistics.mean(_f(_detail(r, "holding_minutes")) for r in rows), 1)
    m.avg_mfe = round(statistics.mean(_f(r.get("mfe")) for r in rows), 2)
    m.avg_mae = round(statistics.mean(_f(r.get("mae")) for r in rows), 2)
    rs = [_f(r.get("r_multiple")) for r in rows if r.get("r_multiple") is not None]
    m.avg_r = round(statistics.mean(rs), 3) if rs else None
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        reasons[str(r.get("exit_reason") or _detail(r, "exit_reason") or "?")] += 1
    m.exit_reasons = dict(sorted(reasons.items()))
    return m


_TOD = ((dt.time(9, 15), "09:15-10:00"), (dt.time(10, 0), "10:00-11:30"), (dt.time(11, 30), "11:30-13:00"),
        (dt.time(13, 0), "13:00-14:00"), (dt.time(14, 0), "14:00-15:00"), (dt.time(15, 0), "15:00-15:30"))


def segment_key(row: dict, key: str) -> str:
    d = row.get("details") or {}
    if key == "time_of_day":
        t = _ts(d.get("entry_ts") or row.get("entry_ts"))
        if t is None:
            return "?"
        label = "?"
        for start, name in _TOD:
            if t.time() >= start:
                label = name
        return label
    if key == "strike":
        return str(d.get("strike", "?"))
    return str(d.get(key, row.get(key, "?")))


def segmented(rows: list[dict], capital: float, keys: tuple[str, ...] = SEGMENT_KEYS) -> dict[str, dict[str, Metrics]]:
    """§77 'segment by': metrics per value of each key."""
    out: dict[str, dict[str, Metrics]] = {}
    for key in keys:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[segment_key(r, key)].append(r)
        out[key] = {k: compute(v, capital) for k, v in sorted(groups.items())}
    return out


@dataclass
class FingerprintStat:
    fingerprint: str
    sample_size: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    profit_factor: float | None
    max_drawdown: float
    avg_mfe: float
    avg_mae: float
    avg_holding_minutes: float
    reliable: bool  # enough samples to lean on (§16 "score is not probability unless calibrated")


def fingerprint_stats(rows: list[dict], capital: float, min_samples: int = 20) -> list[FingerprintStat]:
    """§63: measure per fingerprint. `reliable` is False below min_samples -
    the risk engine's EV should keep using its default win probability for
    those (nothing here feeds live decisions until a human promotes it, §83)."""
    out = []
    for fp, m in segmented(rows, capital, ("fingerprint",))["fingerprint"].items():
        out.append(FingerprintStat(fp, m.trades, m.win_rate, m.avg_win, m.avg_loss, m.expectancy, m.profit_factor,
                                   m.max_drawdown, m.avg_mfe, m.avg_mae, m.avg_holding_minutes, m.trades >= min_samples))
    out.sort(key=lambda s: (-s.sample_size, s.fingerprint))
    return out


def render(m: Metrics, title: str = "") -> str:
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"
    sh = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
    so = f"{m.sortino_daily:.2f}" if m.sortino_daily is not None else "n/a"
    lines = [
        f"{title}trades={m.trades} wins={m.wins} losses={m.losses} win_rate={m.win_rate:.0%} days={m.days}",
        f"gross={m.gross_pnl:+.0f} costs={m.costs:.0f} net={m.net_pnl:+.0f} expectancy={m.expectancy:+.0f}/trade PF={pf}",
        f"avg_win={m.avg_win:+.0f} avg_loss={m.avg_loss:+.0f} max_dd={m.max_drawdown:.0f} ({m.max_drawdown_pct:.1%}) "
        f"recovery={m.recovery_factor if m.recovery_factor is not None else 'n/a'} sharpe_d={sh} sortino_d={so}",
        f"streaks W{m.max_win_streak}/L{m.max_loss_streak} hold={m.avg_holding_minutes:.0f}m mfe={m.avg_mfe:+.0f} mae={m.avg_mae:+.0f}"
        + (f" avgR={m.avg_r:+.2f}" if m.avg_r is not None else ""),
    ]
    if m.exit_reasons:
        lines.append("exits: " + ", ".join(f"{k}={v}" for k, v in m.exit_reasons.items()))
    return "\n".join(lines)
