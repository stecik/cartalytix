"""Bounded process-local login continuations; credentials stay in memory only."""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal
from uuid import UUID

from .session import FoodoraError, FoodoraSession, execute


@dataclass(frozen=True, repr=False)
class LoginOutcome:
    step: Literal["method", "otp", "completed"]
    flow_id: str | None = None
    methods: tuple[str, ...] = ()
    method: str | None = None
    session: FoodoraSession | None = None
    email: str | None = None


@dataclass(repr=False)
class LoginSession:
    owner_id: UUID
    connection_id: UUID
    email: str
    password: str
    expires_at: float
    cookies: dict[str, str] = field(default_factory=dict)
    methods: tuple[str, ...] = ()
    method: str | None = None
    step: Literal["login", "method", "otp"] = "login"
    attempts: int = 0
    lock: Lock = field(default_factory=Lock)

    def clear(self) -> None:
        self.password = ""
        self.cookies.clear()
        self.email = ""


class LoginSessions:
    """One API process owns continuations until completion, cancellation or expiry."""

    def __init__(
        self,
        *,
        transport: Callable[[dict[str, Any]], dict[str, Any]] = execute,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 600,
        maximum: int = 16,
    ) -> None:
        self._transport, self._clock = transport, clock
        self._ttl, self._maximum = ttl, maximum
        self._sessions: dict[str, LoginSession] = {}
        self._guard = Lock()

    def start(self, owner: UUID, connection: UUID, email: str, password: str) -> LoginOutcome:
        self.cleanup()
        with self._guard:
            if len(self._sessions) >= self._maximum or any(
                session.connection_id == connection for session in self._sessions.values()
            ):
                raise FoodoraError("login_busy")
            identifier = secrets.token_urlsafe(32)
            session = LoginSession(owner, connection, email, password, self._clock() + self._ttl)
            session.lock.acquire()
            self._sessions[identifier] = session
        return self._run(identifier, session, "login")

    def send(self, owner: UUID, connection: UUID, identifier: str, method: str) -> LoginOutcome:
        session = self._acquire(owner, connection, identifier)
        return self._run(identifier, session, "send", method=method)

    def verify(self, owner: UUID, connection: UUID, identifier: str, code: str) -> LoginOutcome:
        session = self._acquire(owner, connection, identifier)
        return self._run(identifier, session, "verify", code=code)

    def cancel(self, owner: UUID, connection: UUID, identifier: str) -> None:
        with self._guard:
            session = self._sessions.get(identifier)
            if session is None or session.owner_id != owner or session.connection_id != connection:
                raise FoodoraError("login_expired")
            self._sessions.pop(identifier)
            session.clear()

    def cancel_connection(self, owner: UUID, connection: UUID) -> None:
        with self._guard:
            for identifier, session in list(self._sessions.items()):
                if session.owner_id == owner and session.connection_id == connection:
                    self._sessions.pop(identifier)
                    session.clear()

    def _acquire(self, owner: UUID, connection: UUID, identifier: str) -> LoginSession:
        self.cleanup()
        with self._guard:
            session = self._sessions.get(identifier)
            if (
                session is None
                or session.owner_id != owner
                or session.connection_id != connection
                or session.expires_at <= self._clock()
            ):
                raise FoodoraError("login_expired")
            if not session.lock.acquire(blocking=False):
                raise FoodoraError("login_busy")
            return session

    def _require_live(self, identifier: str, session: LoginSession) -> None:
        if self._sessions.get(identifier) is not session or session.expires_at <= self._clock():
            raise FoodoraError("login_expired")

    def _run(
        self,
        identifier: str,
        session: LoginSession,
        operation: Literal["login", "send", "verify"],
        *,
        method: str | None = None,
        code: str | None = None,
    ) -> LoginOutcome:
        try:
            with self._guard:
                self._require_live(identifier, session)
                if operation == "send":
                    if session.step != "method" or method not in session.methods:
                        raise FoodoraError("login_flow_incomplete")
                    session.method = method
                elif operation == "verify":
                    if session.step != "otp" or not code:
                        raise FoodoraError("login_flow_incomplete")
                    session.attempts += 1
                payload: dict[str, Any] = {
                    "command": "login" if operation == "login" else "otp",
                    "username": session.email,
                    "password": session.password,
                }
                if operation != "login":
                    payload.update(cookies=session.cookies.copy(), method=session.method)
                if operation == "verify":
                    payload["otp"] = code
            try:
                result = self._transport(payload)
            finally:
                payload.clear()
            with self._guard:
                self._require_live(identifier, session)
                return self._outcome(identifier, session, operation, result)
        except FoodoraError as error:
            with self._guard:
                live = (
                    self._sessions.get(identifier) is session and session.expires_at > self._clock()
                )
                if not live:
                    self._discard(identifier, session)
                    raise FoodoraError("login_expired") from None
                if str(error) != "otp_rejected" or operation != "verify" or session.attempts >= 3:
                    self._discard(identifier, session)
            raise
        except Exception:
            with self._guard:
                self._discard(identifier, session)
            raise FoodoraError("provider_unavailable") from None
        finally:
            session.lock.release()

    def _outcome(
        self,
        identifier: str,
        session: LoginSession,
        operation: Literal["login", "send", "verify"],
        result: dict[str, Any],
    ) -> LoginOutcome:
        if result.get("status") == "otp_required":
            if operation == "verify":
                raise FoodoraError("otp_rejected")
            cookies = result.get("cookies")
            if not isinstance(cookies, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in cookies.items()
            ):
                raise FoodoraError("provider_response_invalid")
            session.cookies = cookies.copy()
            if operation == "login":
                offered = result.get("methods")
                if not isinstance(offered, list):
                    raise FoodoraError("provider_response_invalid")
                session.methods = tuple(
                    dict.fromkeys(
                        method for method in offered if method in ("EMAIL", "SMS", "CALL")
                    )
                )
                if not session.methods:
                    raise FoodoraError("provider_response_invalid")
                session.step = "method"
                return LoginOutcome("method", identifier, session.methods)
            session.step = "otp"
            return LoginOutcome("otp", identifier, method=session.method)
        report = result.get("report")
        if not isinstance(report, dict) or report.get("authenticated") is not True:
            raise FoodoraError("provider_response_invalid")
        credentials = FoodoraSession.from_payload(result.get("session"))
        outcome = LoginOutcome("completed", session=credentials, email=session.email)
        self._discard(identifier, session)
        return outcome

    def _discard(self, identifier: str, session: LoginSession) -> None:
        if self._sessions.get(identifier) is session:
            self._sessions.pop(identifier)
        session.clear()

    def cleanup(self) -> None:
        with self._guard:
            for identifier, session in list(self._sessions.items()):
                if session.expires_at <= self._clock():
                    self._discard(identifier, session)

    def close(self) -> None:
        with self._guard:
            for identifier, session in list(self._sessions.items()):
                self._discard(identifier, session)
