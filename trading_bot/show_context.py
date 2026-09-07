import json
import logging

from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.market_context import snapshot
from trading_bot.rest_client import RestClient


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    session = Session(cfg)
    session.login()
    rest = RestClient(session)

    ctx = snapshot(rest)

    print(f"\nIndia VIX: {ctx.india_vix}")
    print(f"\nPCR: {json.dumps(ctx.pcr, indent=2)}")
    print(f"\nOI Buildup: {json.dumps(ctx.oi_buildup, indent=2)}")
    print(f"\nGainers/Losers (by OI): {json.dumps(ctx.gainers_losers, indent=2)}")
    print(f"\nGlobal cues: {json.dumps(ctx.global_quotes, indent=2)}")
    print(f"\nMacro (World Bank, annual/lagged - background only): {json.dumps(ctx.macro, indent=2)}")
    print(f"\nNews headlines: {'not configured (set NEWS_API_KEY)' if ctx.news is None else len(ctx.news)}")
    print(f"Economic calendar: {'not configured (set FINNHUB_API_KEY)' if ctx.economic_calendar is None else len(ctx.economic_calendar)}")

    session.logout()


if __name__ == "__main__":
    main()
