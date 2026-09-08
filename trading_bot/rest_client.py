import logging

import requests

from trading_bot.auth import Session

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message: str, errorcode: str = ""):
        super().__init__(f"{errorcode}: {message}" if errorcode else message)
        self.errorcode = errorcode


class RestClient:
    """Thin wrapper around the SmartAPI REST endpoints used by the bot.

    Order placement/modification/cancellation refuses to run unless
    session.cfg.dry_run is False, so a paper-mode run can never send a
    real order by accident.
    """

    def __init__(self, session: Session):
        self.session = session

    def _headers(self) -> dict:
        return self.session.headers(authenticated=True)

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(
            method,
            f"{self.session.cfg.root_url}{path}",
            headers=self._headers(),
            timeout=10,
            **kwargs,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise ApiError(payload.get("message", "request failed"), payload.get("errorcode", ""))
        return payload["data"]

    # --- account ---

    def get_profile(self) -> dict:
        return self._call("GET", "/rest/secure/angelbroking/user/v1/getProfile")

    def get_rms(self) -> dict:
        return self._call("GET", "/rest/secure/angelbroking/user/v1/getRMS")

    # --- market data ---

    def get_ltp(self, exchange: str, tradingsymbol: str, symboltoken: str) -> dict:
        return self._call(
            "POST",
            "/rest/secure/angelbroking/order/v1/getLtpData",
            json={"exchange": exchange, "tradingsymbol": tradingsymbol, "symboltoken": symboltoken},
        )

    def get_margin(self, positions: list[dict]) -> dict:
        """Read-only margin estimate - safe to call even in dry-run, places no order."""
        return self._call("POST", "/rest/secure/angelbroking/margin/v1/batch", json={"positions": positions})

    def get_candle_data(self, exchange: str, symboltoken: str, interval: str, fromdate: str, todate: str) -> list:
        return self._call(
            "POST",
            "/rest/secure/angelbroking/historical/v1/getCandleData",
            json={
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": interval,
                "fromdate": fromdate,
                "todate": todate,
            },
        )

    def get_oi_data(self, exchange: str, symboltoken: str, interval: str, fromdate: str, todate: str) -> list:
        """Historical OI time series for a LIVE F&O contract (docs: "for live
        F&O contracts only") - separate from get_candle_data, which returns
        OHLCV but no OI. Returns data[] = [{"time": ..., "oi": ...}, ...]."""
        return self._call(
            "POST",
            "/rest/secure/angelbroking/historical/v1/getOIData",
            json={
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": interval,
                "fromdate": fromdate,
                "todate": todate,
            },
        )

    def get_quote(self, mode: str, exchange_tokens: dict[str, list[str]]) -> dict:
        """Batch quote (up to 50 symbols per exchange per request per docs).
        mode: LTP, OHLC, or FULL."""
        return self._call(
            "POST",
            "/rest/secure/angelbroking/market/v1/quote/",
            json={"mode": mode, "exchangeTokens": exchange_tokens},
        )

    # --- market-wide breadth/sentiment (NSE derivatives only) ---

    def get_pcr(self) -> list:
        return self._call("GET", "/rest/secure/angelbroking/marketData/v1/putCallRatio")

    def get_oi_buildup(self, expirytype: str, datatype: str) -> list:
        return self._call(
            "POST",
            "/rest/secure/angelbroking/marketData/v1/OIBuildup",
            json={"expirytype": expirytype, "datatype": datatype},
        )

    def get_gainers_losers(self, datatype: str, expirytype: str) -> list:
        return self._call(
            "POST",
            "/rest/secure/angelbroking/marketData/v1/gainersLosers",
            json={"datatype": datatype, "expirytype": expirytype},
        )

    # --- orders (read-only, always safe) ---

    def get_order_book(self) -> list:
        return self._call("GET", "/rest/secure/angelbroking/order/v1/getOrderBook")

    def get_trade_book(self) -> list:
        return self._call("GET", "/rest/secure/angelbroking/order/v1/getTradeBook")

    # --- orders (side-effecting, guarded by dry_run) ---

    def place_order(self, order: dict) -> dict:
        if self.session.cfg.dry_run:
            log.info("[DRY RUN] would place order: %s", order)
            return {"dry_run": True, "order": order}
        return self._call("POST", "/rest/secure/angelbroking/order/v1/placeOrder", json=order)

    def modify_order(self, order: dict) -> dict:
        if self.session.cfg.dry_run:
            log.info("[DRY RUN] would modify order: %s", order)
            return {"dry_run": True, "order": order}
        return self._call("POST", "/rest/secure/angelbroking/order/v1/modifyOrder", json=order)

    def cancel_order(self, variety: str, orderid: str) -> dict:
        if self.session.cfg.dry_run:
            log.info("[DRY RUN] would cancel order: variety=%s orderid=%s", variety, orderid)
            return {"dry_run": True, "orderid": orderid}
        return self._call(
            "POST",
            "/rest/secure/angelbroking/order/v1/cancelOrder",
            json={"variety": variety, "orderid": orderid},
        )
