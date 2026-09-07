import logging

import pyotp
import requests

from trading_bot.config import Config

log = logging.getLogger(__name__)


class AuthError(RuntimeError):
    def __init__(self, message: str, errorcode: str = ""):
        super().__init__(f"{errorcode}: {message}" if errorcode else message)
        self.errorcode = errorcode


class Session:
    """Holds tokens from a SmartAPI login and can refresh the JWT."""

    def __init__(self, cfg: Config):
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
        body = {
            "clientcode": self.cfg.client_code,
            "password": self.cfg.pin,
            "totp": totp,
        }
        resp = requests.post(
            f"{self.cfg.root_url}/rest/auth/angelbroking/user/v1/loginByPassword",
            json=body,
            headers=self.headers(authenticated=False),
            timeout=10,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise AuthError(payload.get("message", "login failed"), payload.get("errorcode", ""))

        data = payload["data"]
        self.jwt_token = data["jwtToken"]
        self.refresh_token = data["refreshToken"]
        self.feed_token = data["feedToken"]
        log.info("Logged in as %s", self.cfg.client_code)

    def refresh(self) -> None:
        resp = requests.post(
            f"{self.cfg.root_url}/rest/auth/angelbroking/jwt/v1/generateTokens",
            json={"refreshToken": self.refresh_token},
            headers=self.headers(authenticated=True),
            timeout=10,
        )
        payload = resp.json()
        if not payload.get("status"):
            raise AuthError(payload.get("message", "token refresh failed"), payload.get("errorcode", ""))

        data = payload["data"]
        self.jwt_token = data["jwtToken"]
        self.refresh_token = data["refreshToken"]
        self.feed_token = data["feedToken"]
        log.info("Refreshed session tokens")

    def logout(self) -> None:
        requests.post(
            f"{self.cfg.root_url}/rest/secure/angelbroking/user/v1/logout",
            json={"clientcode": self.cfg.client_code},
            headers=self.headers(authenticated=True),
            timeout=10,
        )
        log.info("Logged out")
