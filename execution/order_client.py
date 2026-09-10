"""Order placement + order-book status. Reuses data/broker_client.py's
proven Session/auth (not duplicated) - this module only adds the
order-side endpoints.

The live/simulated gate lives HERE, in the client itself, not left to
callers to remember - mirrors main's trading_bot/rest_client.py, whose
own dry_run guard exists in exactly the same place for the same reason:
a caller-side "if not dry_run: place_order()" check is one missed `if`
away from a real order. place_order() refuses to call the real broker
endpoint unless environment == "live" AND dry_run is False - BOTH must
agree; codex is nowhere near a live go-live gate yet (see README.md),
so this always simulates today, and will keep doing so until both
flags are deliberately changed together.
"""
import logging

import requests

from data.broker_client import ApiError, Session

log = logging.getLogger(__name__)


class OrderClient:
    def __init__(self, session: Session, environment: str, dry_run: bool):
        self.session = session
        self.environment = environment
        self.dry_run = dry_run

    @property
    def live_trading_enabled(self) -> bool:
        return self.environment == "live" and not self.dry_run

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(
            method, f"{self.session.cfg.root_url}{path}", headers=self.session.headers(authenticated=True),
            timeout=10, **kwargs,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise ApiError(payload.get("message", "request failed"), payload.get("errorcode", ""))
        return payload.get("data")

    def place_order(self, order: dict) -> dict:
        if not self.live_trading_enabled:
            log.info("[SIMULATED] would place order (environment=%s dry_run=%s): %s", self.environment, self.dry_run, order)
            return {"simulated": True, "order": order}
        return self._call("POST", "/rest/secure/angelbroking/order/v1/placeOrder", json=order)

    def get_order_book(self) -> list:
        """Read-only, always calls the real broker regardless of dry_run/
        environment - checking existing order status is never a
        side-effecting action, and simulated runs have nothing to check
        here anyway (place_order never submitted anything)."""
        return self._call("GET", "/rest/secure/angelbroking/order/v1/getOrderBook") or []
