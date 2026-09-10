"""SmartAPI auth + historical-candle client.

Adapted from the proven, production trading_bot/auth.py + rest_client.py
pattern on `main` (same login flow, same required X-Client*/X-MAC headers,
same session-invalidation retry-once behavior) rather than reinventing
it. Deliberately scoped down to what Phase 2 (historical data pipeline)
needs - login/refresh/logout and getCandleData only. Order placement,
LTP, margin, etc. belong to the execution phase, not here.

Session-sharing note (learned live on `main`): Angel One issues one API
key per ACCOUNT, not per app, and codex reuses that same account/key.
Logging in here CAN invalidate a concurrently-running bot's JWT, and
vice versa - the auto-relogin-and-retry-once behavior below turns that
into a brief hiccup rather than a silent, indefinite outage.
"""
import logging
import socket
import uuid
from dataclasses import dataclass, field

import pyotp
import requests

log = logging.getLogger(__name__)

SESSION_ERROR_CODES = {"AB1010", "AB1011", "AB9005"}


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _public_ip(fallback: str) -> str:
    try:
        resp = requests.get("https://api.ipify.org", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except requests.RequestException:
        return fallback


def _mac_address() -> str:
    mac = uuid.getnode()
    return "-".join(f"{(mac >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


@dataclass(frozen=True)
class BrokerConfig:
    api_key: str
    client_code: str
    pin: str
    totp_secret: str
    local_ip: str = field(default_factory=_local_ip)
    public_ip: str = field(default_factory=lambda: _public_ip("127.0.0.1"))
    mac_address: str = field(default_factory=_mac_address)
    root_url: str = "https://apiconnect.angelone.in"
    scrip_master_url: str = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

    @classmethod
    def from_settings(cls, settings) -> "BrokerConfig":
        return cls(
            api_key=settings.smartapi_key,
            client_code=settings.smartapi_client_code,
            pin=settings.smartapi_pin,
            totp_secret=settings.smartapi_totp_secret,
        )


class AuthError(RuntimeError):
    def __init__(self, message: str, errorcode: str = ""):
        super().__init__(f"{errorcode}: {message}" if errorcode else message)
        self.errorcode = errorcode


class ApiError(RuntimeError):
    def __init__(self, message: str, errorcode: str = ""):
        super().__init__(f"{errorcode}: {message}" if errorcode else message)
        self.errorcode = errorcode


class Session:
    """Holds tokens from a SmartAPI login and can refresh/logout."""

    def __init__(self, cfg: BrokerConfig):
        self.cfg = cfg
        self.jwt_token: str | None = None
        self.refresh_token: str | None = None
        self.feed_token: str | None = None

    def headers(self, authenticated: bool = True) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self.cfg.local_ip,
            "X-ClientPublicIP": self.cfg.public_ip,
            "X-MACAddress": self.cfg.mac_address,
            "X-PrivateKey": self.cfg.api_key,
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        return headers

    def login(self) -> None:
        totp = pyotp.TOTP(self.cfg.totp_secret).now()
        body = {"clientcode": self.cfg.client_code, "password": self.cfg.pin, "totp": totp}
        resp = requests.post(
            f"{self.cfg.root_url}/rest/auth/angelbroking/user/v1/loginByPassword",
            json=body, headers=self.headers(authenticated=False), timeout=10,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise AuthError(payload.get("message", "login failed"), payload.get("errorcode", ""))
        data = payload["data"]
        self.jwt_token = data["jwtToken"]
        self.refresh_token = data["refreshToken"]
        self.feed_token = data["feedToken"]
        log.info("Logged in as %s", self.cfg.client_code)

    def logout(self) -> None:
        requests.post(
            f"{self.cfg.root_url}/rest/secure/angelbroking/user/v1/logout",
            json={"clientcode": self.cfg.client_code}, headers=self.headers(authenticated=True), timeout=10,
        )
        log.info("Logged out")


class HistoricalDataClient:
    """Thin wrapper around getCandleData, with session-error auto-relogin -
    safe to retry here since a read-only GET-style call is idempotent
    (unlike order placement, which never blindly retries)."""

    def __init__(self, session: Session):
        self.session = session

    def get_candle_data(self, exchange: str, symboltoken: str, interval: str, fromdate: str, todate: str) -> list:
        try:
            return self._call_once(exchange, symboltoken, interval, fromdate, todate)
        except ApiError as e:
            if e.errorcode not in SESSION_ERROR_CODES:
                raise
            log.warning("Session error (%s) - re-logging in and retrying once", e.errorcode)
            self.session.login()
            return self._call_once(exchange, symboltoken, interval, fromdate, todate)

    def _call_once(self, exchange: str, symboltoken: str, interval: str, fromdate: str, todate: str) -> list:
        resp = requests.post(
            f"{self.session.cfg.root_url}/rest/secure/angelbroking/historical/v1/getCandleData",
            json={"exchange": exchange, "symboltoken": symboltoken, "interval": interval, "fromdate": fromdate, "todate": todate},
            headers=self.session.headers(authenticated=True), timeout=10,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise ApiError(payload.get("message", "request failed"), payload.get("errorcode", ""))
        return payload["data"]
