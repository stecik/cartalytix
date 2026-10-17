"""Bounded, process-local OAuth continuations; secrets never enter persistent storage."""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

from .login import LidlLogin
from .login_protocol import LoginError, Status


class LoginClient(Protocol):
    def begin(self) -> Status: ...
    def credentials(self, email: str, password: str) -> Status: ...
    def verify(self, code: str) -> Status: ...
    def take_refresh_token(self) -> str: ...
    def close(self) -> None: ...


@dataclass(frozen=True, repr=False)
class LoginOutcome:
    step: Literal["otp", "completed"]
    flow_id: str | None = None
    method: str | None = None
    refresh_token: str | None = None


@dataclass(repr=False)
class LoginSession:
    owner_id: UUID
    connection_id: UUID
    client: LoginClient
    expires_at: float
    lock: Lock = field(default_factory=Lock)


class LoginSessions:
    """One API process owns its flows; restart/expiry requires a fresh login."""

    def __init__(
        self,
        *,
        factory: Callable[[], LoginClient] = LidlLogin,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 600,
        maximum: int = 16,
    ) -> None:
        self._factory, self._clock = factory, clock
        self._ttl, self._maximum = ttl, maximum
        self._sessions: dict[str, LoginSession] = {}
        self._guard = Lock()

    def start(self, owner: UUID, connection: UUID, email: str, password: str) -> LoginOutcome:
        self.cleanup()
        with self._guard:
            if len(self._sessions) >= self._maximum or any(
                session.connection_id == connection for session in self._sessions.values()
            ):
                raise LoginError("login_busy")
            identifier = secrets.token_urlsafe(32)
            try:
                client = self._factory()
            except Exception:
                raise LoginError("login_unavailable") from None
            session = LoginSession(owner, connection, client, self._clock() + self._ttl)
            self._sessions[identifier] = session

        def submit(client: LoginClient) -> Status:
            if client.begin().step != "password":
                raise LoginError("login_flow_incomplete")
            if session.expires_at <= self._clock():
                raise LoginError("login_expired")
            return client.credentials(email, password)

        return self._run(owner, connection, identifier, submit)

    def verify(self, owner: UUID, connection: UUID, identifier: str, code: str) -> LoginOutcome:
        return self._run(owner, connection, identifier, lambda client: client.verify(code))

    def cancel(self, owner: UUID, connection: UUID, identifier: str) -> None:
        session = self._acquire(owner, connection, identifier)
        try:
            self._discard(identifier, session)
        finally:
            session.lock.release()

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
                raise LoginError("login_expired")
            if not session.lock.acquire(blocking=False):
                raise LoginError("login_busy")
            return session

    def _run(
        self,
        owner: UUID,
        connection: UUID,
        identifier: str,
        operation: Callable[[LoginClient], Status],
    ) -> LoginOutcome:
        session = self._acquire(owner, connection, identifier)
        try:
            status = operation(session.client)
            if session.expires_at <= self._clock():
                raise LoginError("login_expired")
            if status.step == "otp":
                return LoginOutcome("otp", identifier, status.method)
            if status.step != "completed":
                raise LoginError("login_flow_incomplete")
            token = session.client.take_refresh_token()
            self._discard(identifier, session)
            return LoginOutcome("completed", refresh_token=token)
        except LoginError:
            self._discard(identifier, session)
            raise
        except Exception:
            self._discard(identifier, session)
            raise LoginError("login_unavailable") from None
        finally:
            session.lock.release()

    def _discard(self, identifier: str, session: LoginSession) -> None:
        with self._guard:
            self._sessions.pop(identifier, None)
        session.client.close()

    def cleanup(self) -> None:
        expired: list[LoginSession] = []
        with self._guard:
            for identifier, session in list(self._sessions.items()):
                if session.expires_at <= self._clock() and session.lock.acquire(blocking=False):
                    self._sessions.pop(identifier)
                    expired.append(session)
        for session in expired:
            try:
                session.client.close()
            finally:
                session.lock.release()

    def close(self) -> None:
        with self._guard:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.client.close()
