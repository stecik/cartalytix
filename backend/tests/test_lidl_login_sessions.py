from uuid import uuid4

import pytest

from cartalytix.providers.lidl.login_protocol import LoginError, Status
from cartalytix.providers.lidl.login_sessions import LoginSessions


class FakeLogin:
    def __init__(self, *, otp: bool = True, fail: bool = False) -> None:
        self.otp = otp
        self.fail = fail
        self.closed = False
        self.posts = 0

    def begin(self) -> Status:
        return Status("password")

    def credentials(self, email: str, password: str) -> Status:
        self.posts += 1
        assert email == "owner@example.com" and password == "private-password"
        if self.fail:
            raise LoginError("login_request_uncertain")
        return Status("otp", "Email") if self.otp else Status("completed")

    def verify(self, code: str) -> Status:
        self.posts += 1
        assert code == "123456"
        return Status("completed")

    def take_refresh_token(self) -> str:
        return "private-refresh"

    def close(self) -> None:
        self.closed = True


def test_mfa_is_bound_to_owner_and_connection_and_consumed_once() -> None:
    login = FakeLogin()
    sessions = LoginSessions(factory=lambda: login)
    owner, connection = uuid4(), uuid4()
    outcome = sessions.start(owner, connection, "owner@example.com", "private-password")
    assert outcome.step == "otp" and outcome.flow_id
    assert "private" not in repr(outcome)
    for other_owner, other_connection in [(uuid4(), connection), (owner, uuid4())]:
        with pytest.raises(LoginError, match="login_expired"):
            sessions.verify(other_owner, other_connection, outcome.flow_id, "123456")
    assert not login.closed
    completed = sessions.verify(owner, connection, outcome.flow_id, "123456")
    assert completed.step == "completed" and completed.refresh_token == "private-refresh"
    assert login.closed
    with pytest.raises(LoginError, match="login_expired"):
        sessions.verify(owner, connection, outcome.flow_id, "123456")
    assert login.posts == 2


def test_expiry_closes_abandoned_login_without_user_request() -> None:
    now = [0.0]
    login = FakeLogin()
    sessions = LoginSessions(factory=lambda: login, clock=lambda: now[0], ttl=600)
    owner, connection = uuid4(), uuid4()
    outcome = sessions.start(owner, connection, "owner@example.com", "private-password")
    now[0] = 601
    sessions.cleanup()
    assert login.closed
    with pytest.raises(LoginError, match="login_expired"):
        sessions.verify(owner, connection, outcome.flow_id or "", "123456")
    assert login.posts == 1


def test_uncertain_post_closes_state_and_never_automatically_retries() -> None:
    login = FakeLogin(fail=True)
    sessions = LoginSessions(factory=lambda: login)
    with pytest.raises(LoginError, match="login_request_uncertain"):
        sessions.start(uuid4(), uuid4(), "owner@example.com", "private-password")
    assert login.closed and login.posts == 1


def test_cancel_and_shutdown_dispose_clients_and_limit_is_released() -> None:
    logins: list[FakeLogin] = []

    def factory() -> FakeLogin:
        login = FakeLogin()
        logins.append(login)
        return login

    sessions = LoginSessions(factory=factory, maximum=1)
    owner, connection = uuid4(), uuid4()
    outcome = sessions.start(owner, connection, "owner@example.com", "private-password")
    with pytest.raises(LoginError, match="login_busy"):
        sessions.start(uuid4(), uuid4(), "owner@example.com", "private-password")
    sessions.cancel(owner, connection, outcome.flow_id or "")
    assert logins[0].closed
    sessions.start(owner, connection, "owner@example.com", "private-password")
    sessions.close()
    assert all(login.closed for login in logins)


def test_second_login_for_same_connection_does_not_submit_another_password() -> None:
    login = FakeLogin()
    sessions = LoginSessions(factory=lambda: login)
    owner, connection = uuid4(), uuid4()
    sessions.start(owner, connection, "owner@example.com", "private-password")
    with pytest.raises(LoginError, match="login_busy"):
        sessions.start(owner, connection, "owner@example.com", "private-password")
    assert login.posts == 1
    sessions.close()


def test_flow_expiring_during_verification_cannot_handoff_token() -> None:
    now = [0.0]

    class SlowLogin(FakeLogin):
        def verify(self, code: str) -> Status:
            now[0] = 601.0
            return super().verify(code)

    login = SlowLogin()
    sessions = LoginSessions(factory=lambda: login, clock=lambda: now[0])
    owner, connection = uuid4(), uuid4()
    outcome = sessions.start(owner, connection, "owner@example.com", "private-password")
    with pytest.raises(LoginError, match="login_expired"):
        sessions.verify(owner, connection, outcome.flow_id or "", "123456")
    assert login.closed


def test_expiry_during_initialization_never_submits_password() -> None:
    now = [0.0]

    class SlowInitialization(FakeLogin):
        def begin(self) -> Status:
            now[0] = 601.0
            return super().begin()

    login = SlowInitialization()
    sessions = LoginSessions(factory=lambda: login, clock=lambda: now[0])
    with pytest.raises(LoginError, match="login_expired"):
        sessions.start(uuid4(), uuid4(), "owner@example.com", "private-password")
    assert login.posts == 0 and login.closed


def test_simultaneous_verification_does_not_replay_or_close_busy_flow() -> None:
    login = FakeLogin()
    sessions = LoginSessions(factory=lambda: login)
    owner, connection = uuid4(), uuid4()
    outcome = sessions.start(owner, connection, "owner@example.com", "private-password")
    flow = outcome.flow_id or ""
    session = sessions._sessions[flow]
    session.lock.acquire()
    try:
        with pytest.raises(LoginError, match="login_busy"):
            sessions.verify(owner, connection, flow, "123456")
        assert login.posts == 1 and not login.closed
    finally:
        session.lock.release()
    assert sessions.verify(owner, connection, flow, "123456").step == "completed"
