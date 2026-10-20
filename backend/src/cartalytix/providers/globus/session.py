"""Verified Globus web sessions and bounded server-side renewal."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import httpx

from cartalytix.providers.common import ProviderError

SITE = "https://www.globus.cz"
PROFILE_URL = f"{SITE}/api/v1/gsoa/customer/profiles/me/auth-status"
REFRESH_URL = f"{SITE}/api/v1/oidc/session/refresh"
SESSION_PREFIX = "globus-session-v1:"
COOKIE = "oidc_session"
COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,4096}")


class GlobusError(ProviderError):
    pass


@dataclass(frozen=True)
class GlobusAccount:
    subject: str
    email: str | None


@dataclass(frozen=True, repr=False)
class GlobusSession:
    cookie: str
    session_id: str
    subject: str
    user_agent: str
    cookie_expires: float | None = None
    email: str | None = None

    def serialize(self) -> str:
        return SESSION_PREFIX + json.dumps(
            {
                "session_cookie": self.cookie,
                "session_id": self.session_id,
                "subject": self.subject,
                "user_agent": self.user_agent,
                "cookie_expires": self.cookie_expires,
            },
            separators=(",", ":"),
            allow_nan=False,
        )


def parse_session_cookie(value: str) -> str:
    """Retain support for sessions saved by the former cookie-based connector."""
    cookie = value.strip().removeprefix(f"{COOKIE}=")
    if not COOKIE_VALUE.fullmatch(cookie):
        raise GlobusError("session_cookie_invalid")
    return cookie


def decode_session(value: str) -> GlobusSession | None:
    if not value.startswith(SESSION_PREFIX):
        try:
            parse_session_cookie(value)
        except GlobusError:
            raise GlobusError("auth_required") from None
        return None
    try:
        data = json.loads(value.removeprefix(SESSION_PREFIX))
        if not isinstance(data, dict):
            raise ValueError
        values = [
            data.get(key) for key in ("session_cookie", "session_id", "subject", "user_agent")
        ]
        if not all(isinstance(item, str) and item for item in values):
            raise ValueError
        cookie, session_id, subject, user_agent = (str(item) for item in values)
        if (
            not COOKIE_VALUE.fullmatch(cookie)
            or not COOKIE_VALUE.fullmatch(session_id)
            or not subject.strip()
            or len(subject) > 256
            or len(user_agent) > 1024
            or "\r" in user_agent
            or "\n" in user_agent
        ):
            raise ValueError
        expires = data.get("cookie_expires")
        if expires is not None and (
            isinstance(expires, bool)
            or not isinstance(expires, int | float)
            or not math.isfinite(expires)
            or expires <= 0
        ):
            raise ValueError
    except ValueError, TypeError, OverflowError:
        raise GlobusError("auth_required") from None
    return GlobusSession(cookie, session_id, subject, user_agent, expires)


def session_headers(cookie: str, accept: str, user_agent: str = "") -> dict[str, str]:
    headers = {"Accept": accept, "Cookie": f"{COOKIE}={cookie}"}
    if user_agent:
        headers.update({"User-Agent": user_agent, "Origin": SITE, "Referer": f"{SITE}/"})
    return headers


def fetch_identity(
    cookie: str, *, http: httpx.Client | None = None, user_agent: str = ""
) -> GlobusAccount:
    client = http if http is not None else httpx.Client(timeout=30)
    try:
        try:
            response = client.get(
                PROFILE_URL,
                headers=session_headers(cookie, "application/json", user_agent),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise GlobusError("provider_unavailable") from None
    finally:
        if http is None:
            client.close()
    if response.status_code == 401 or response.is_redirect:
        raise GlobusError("session_cookie_rejected")
    if response.status_code == 429:
        raise GlobusError("rate_limited")
    if response.status_code != 200:
        raise GlobusError("provider_unavailable")
    try:
        data = response.json()
    except ValueError:
        raise GlobusError("provider_response_invalid") from None
    if not isinstance(data, dict) or data.get("authenticated") is not True:
        raise GlobusError("session_cookie_rejected")
    profile = data.get("profile")
    registration = profile.get("registration") if isinstance(profile, dict) else None
    contacts = profile.get("contacts") if isinstance(profile, dict) else None
    customer = registration.get("customerID") if isinstance(registration, dict) else None
    if (
        isinstance(customer, bool)
        or not isinstance(customer, int | str)
        or not str(customer).strip()
        or len(str(customer)) > 256
    ):
        raise GlobusError("account_identity_unavailable")
    email = contacts.get("email") if isinstance(contacts, dict) else None
    return GlobusAccount(str(customer), email if isinstance(email, str) and email else None)


def refresh_session(
    session: GlobusSession,
    *,
    http: httpx.Client,
    heartbeat: Callable[[], None] | None = None,
) -> GlobusSession:
    if heartbeat is not None:
        heartbeat()
    try:
        response = http.post(
            REFRESH_URL,
            json={"session_id": session.session_id},
            headers=session_headers(session.cookie, "application/json", session.user_agent),
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise GlobusError("provider_unavailable") from None
    if response.status_code == 401 or response.is_redirect:
        raise GlobusError("auth_required")
    if response.status_code == 429:
        raise GlobusError("rate_limited")
    if response.status_code != 200:
        raise GlobusError("provider_unavailable")
    try:
        data: Any = response.json()
    except ValueError:
        raise GlobusError("provider_response_invalid") from None
    if not isinstance(data, dict) or data.get("error") or data.get("success") is False:
        raise GlobusError("provider_response_invalid")
    session_id = data.get("session_id", session.session_id)
    if not isinstance(session_id, str) or not COOKIE_VALUE.fullmatch(session_id):
        raise GlobusError("provider_response_invalid")
    cookies = [
        cookie
        for cookie in response.cookies.jar
        if cookie.name == COOKIE
        and cookie.domain.lstrip(".") in {"globus.cz", "www.globus.cz"}
        and cookie.path == "/"
    ]
    if len(cookies) > 1:
        raise GlobusError("provider_response_invalid")
    cookie = cookies[0].value if cookies else session.cookie
    if not isinstance(cookie, str) or not COOKIE_VALUE.fullmatch(cookie):
        raise GlobusError("auth_required")
    expires = cookies[0].expires if cookies else session.cookie_expires
    if cookies and expires is not None and expires <= 0:
        raise GlobusError("auth_required")
    candidate = replace(session, cookie=cookie, session_id=session_id, cookie_expires=expires)
    if heartbeat is not None:
        heartbeat()
    try:
        account = fetch_identity(candidate.cookie, http=http, user_agent=candidate.user_agent)
    except GlobusError as error:
        if error.code == "session_cookie_rejected":
            raise GlobusError("auth_required") from None
        raise
    if account.subject != session.subject:
        raise GlobusError("account_mismatch")
    return replace(candidate, email=account.email)
