"""Client for the private endpoints the 10bis website itself calls.

10bis has no public API. Every URL here was taken from the website's own
JavaScript. They are undocumented and can change without notice.
"""
from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

WEB = "https://www.10bis.co.il"
API = "https://api.10bis.co.il/api"

ENDPOINTS = {
    "send_code": WEB + "/NextApi/GetUserAuthenticationDataAndSendAuthenticationCodeToUser",
    "verify_code": WEB + "/NextApi/GetUserV2",
    "report": WEB + "/NextApi/UserTransactionsReport?dateBias=0",
    "refresh": API + "/v1/Authentication/RefreshToken",
    "load_credit": API + "/v3/Payments/LoadTenbisCredit",
}

# Headers the site's api.10bis.co.il client adds to every request.
API_HEADERS = {"x-app-type": "web", "language": "he", "Origin": WEB, "Referer": WEB + "/"}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) tenbis-credit"


class TenbisError(Exception):
    pass


class SessionExpired(TenbisError):
    pass


@dataclass
class Card:
    """A budget card whose leftover can be loaded into 10bis Credit."""
    encrypted_id: str
    suffix: str
    available: float


def format_amount(amount: float) -> str:
    """The site sends amounts as strings: 35 -> "35", 35.5 -> "35.5"."""
    return f"{amount:.2f}".rstrip("0").rstrip(".")


class Client:
    def __init__(self, cookie_path: str, endpoints: dict | None = None, opener=None):
        self.cookie_path = cookie_path
        self.endpoints = {**ENDPOINTS, **(endpoints or {})}
        self.jar = http.cookiejar.LWPCookieJar(cookie_path)
        if os.path.exists(cookie_path):
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    # ---------- transport ----------

    def _save_cookies(self):
        os.makedirs(os.path.dirname(self.cookie_path), exist_ok=True)
        self.jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(self.cookie_path, 0o600)

    def request(self, method: str, url: str, body=None, headers: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json, text/plain, */*")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=30) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SessionExpired(f"HTTP {e.code} from {url}") from e
            raise TenbisError(f"HTTP {e.code} from {url}: {_error_text(e.read().decode())}") from e
        except urllib.error.URLError as e:
            raise TenbisError(f"Network error calling {url}: {e.reason}") from e
        self._save_cookies()

        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
        if isinstance(payload, dict):
            if payload.get("code") == "INVALID_REQUEST":
                raise TenbisError(f"10bis rejected the request: {payload.get('description')}")
            if payload.get("Success") is False:
                errors = json.dumps(payload.get("Errors"), ensure_ascii=False)
                if re.search(r"login|auth|התחבר", errors, re.I):
                    raise SessionExpired(errors)
                raise TenbisError(f"10bis error: {errors}")
        return payload

    # ---------- login ----------

    def send_code(self, email: str) -> dict:
        """Ask 10bis to send a one-time code. Returns data needed by verify_code."""
        resp = self.request("POST", self.endpoints["send_code"],
                            {"culture": "he-IL", "uiCulture": "he", "email": email})
        auth = ((resp or {}).get("Data") or {}).get("codeAuthenticationData")
        if not auth:
            raise TenbisError("10bis did not start a login for this email. Is it registered with 10bis?")
        return auth

    def verify_code(self, email: str, auth: dict, code: str):
        self.request("POST", self.endpoints["verify_code"],
                     {"culture": "he-IL", "uiCulture": "he", "email": email,
                      **auth, "authenticationCode": code.strip(), "shoppingCartGuid": None})

    def refresh(self):
        """Extend the session, as the website does. Called before every run."""
        self.request("POST", self.endpoints["refresh"], {}, API_HEADERS)

    def session_expiry(self) -> dt.datetime | None:
        for c in self.jar:
            if c.name == "Authorization" and c.expires:
                return dt.datetime.fromtimestamp(c.expires)
        return None

    # ---------- budget ----------

    def report(self) -> dict:
        return self.request("GET", self.endpoints["report"])

    def convertible_cards(self) -> list[Card]:
        cards = []
        for c in ((self.report() or {}).get("Data") or {}).get("moneycards") or []:
            conv = c.get("tenbisCreditConversion") or {}
            if c.get("isTenbisCredit") or c.get("cardDeleted") or not conv.get("isEnabled"):
                continue
            cards.append(Card(c["encryptedMoneycardID"], str(c.get("cardSuffix", "")),
                              float(conv.get("availableAmount") or 0)))
        return cards

    def load_credit(self, card: Card, amount: float):
        """What the site's "load to credit" button does."""
        self.request("PATCH", self.endpoints["load_credit"],
                     {"amount": format_amount(amount), "encryptedMoneycardIdToCharge": card.encrypted_id},
                     API_HEADERS)


def _error_text(body: str) -> str:
    try:
        return json.loads(body).get("description") or body[:300]
    except (json.JSONDecodeError, AttributeError):
        return body[:300]
