import logging

import requests

from trading_bot.auth import Session

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message: str, errorcode: str = ""):
        super().__init__(f"{errorcode}: {message}" if errorcode else message)
        self.errorcode = errorcode


# Session-invalidation error codes (docs/smartapi-reference.md's error table):
# AB1010 "AMX Session Expired", AB1011 "Client not login", AB9005 "Invalid
# Session ID". Added specifically because a second bot process now shares
# the SAME api_key as this one (Angel One's developer portal turned out to
# issue one key per account, not per "app" - confirmed live 2026-09-09) -
# a second login CAN invalidate this session's JWT. Auto-relogin-and-retry
# turns that into a brief (~one request) hiccup instead of a silent,
# indefinite outage (nothing previously ever retried a session error).
SESSION_ERROR_CODES = {"AB1010", "AB1011", "AB9005"}


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

    def _call(self, method: str, path: str, retry_on_session_error: bool = True, **kwargs) -> dict:
        try:
            return self._call_once(method, path, **kwargs)
        except ApiError as e:
            if e.errorcode not in SESSION_ERROR_CODES:
                raise
            if not retry_on_session_error:
                # place/modify/cancelOrder: NEVER blindly resubmit here - if
                # the broker's own session-invalidation race meant the order
                # was actually accepted just before the JWT died, an
                # automatic retry would place/modify/cancel it a second
                # time. Re-login so the NEXT call succeeds, but re-raise this
                # one so the caller's own try/except (which already treats
                # any order failure as "MANUAL INTERVENTION NEEDED" - see
                # run_daily.py's _settle_close/_maybe_enter) surfaces it for
                # a human to check the order/trade book before deciding.
                log.warning("Session error (%s) on a mutating order call - re-logging in, NOT auto-retrying the order", e.errorcode)
                self.session.login()
                raise
            log.warning("Session error (%s) - re-logging in and retrying once", e.errorcode)
            self.session.login()
            return self._call_once(method, path, **kwargs)

    def _call_once(self, method: str, path: str, **kwargs) -> dict:
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

    def get_option_greeks(self, name: str, expirydate: str) -> list:
        """Per-strike delta/gamma/theta/vega/impliedVolatility for one
        underlying+expiry, NSE only (docs). expirydate format: "DDMMMYYYY"."""
        return self._call(
            "POST",
            "/rest/secure/angelbroking/marketData/v1/optionGreek",
            json={"name": name, "expirydate": expirydate},
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
        return self._call("POST", "/rest/secure/angelbroking/order/v1/placeOrder", json=order, retry_on_session_error=False)

    def modify_order(self, order: dict) -> dict:
        if self.session.cfg.dry_run:
            log.info("[DRY RUN] would modify order: %s", order)
            return {"dry_run": True, "order": order}
        return self._call("POST", "/rest/secure/angelbroking/order/v1/modifyOrder", json=order, retry_on_session_error=False)

    def cancel_order(self, variety: str, orderid: str) -> dict:
        if self.session.cfg.dry_run:
            log.info("[DRY RUN] would cancel order: variety=%s orderid=%s", variety, orderid)
            return {"dry_run": True, "orderid": orderid}
        return self._call(
            "POST",
            "/rest/secure/angelbroking/order/v1/cancelOrder",
            json={"variety": variety, "orderid": orderid},
            retry_on_session_error=False,
        )
