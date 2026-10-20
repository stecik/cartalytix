import base64
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest

from cartalytix.providers.foodora.login_sessions import LoginSessions
from cartalytix.providers.foodora.session import FoodoraError

OWNER = UUID(int=1)
CONNECTION = UUID(int=2)
PASSWORD = "private-password"
COOKIES = {"preauth": "private-cookie"}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Transport:
    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload.copy())
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def challenge() -> dict[str, Any]:
    return {"status": "otp_required", "methods": ["SMS", "EMAIL"], "cookies": COOKIES.copy()}


def authenticated() -> dict[str, Any]:
    claims = base64.urlsafe_b64encode(
        json.dumps({"user_id": "42", "expires": 4102444800}).encode()
    ).decode().rstrip("=")
    return {
        "session": {
            "access_token": f"header.{claims}.signature",
            "refresh_token": "private-refresh",
            "device_token": "private-device",
            "subject": "42",
            "expires": 4102444800,
        },
        "report": {"authenticated": True},
    }


def begin(transport: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[LoginSessions, str]:
    sessions = LoginSessions(transport=transport)
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.flow_id is not None
    return sessions, outcome.flow_id


def test_login_waits_for_explicit_method_before_sending_code() -> None:
    transport = Transport(challenge(), challenge())
    sessions, identifier = begin(transport)
    assert transport.calls == [
        {"command": "login", "username": "me@example.com", "password": PASSWORD}
    ]
    outcome = sessions.send(OWNER, CONNECTION, identifier, "EMAIL")
    assert outcome.step == "otp"
    assert outcome.method == "EMAIL"
    assert transport.calls[-1] == {
        "command": "otp",
        "username": "me@example.com",
        "password": PASSWORD,
        "cookies": COOKIES,
        "method": "EMAIL",
    }
    assert PASSWORD not in repr(outcome)
    assert "private-cookie" not in repr(outcome)


def test_password_login_can_complete_without_verification() -> None:
    sessions = LoginSessions(transport=Transport(authenticated()))
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.step == "completed"
    assert outcome.email == "me@example.com"
    assert outcome.flow_id is None
    assert outcome.session is not None
    assert outcome.session.subject == "42"
    assert sessions._sessions == {}
    assert "private-refresh" not in repr(outcome)


def test_verified_login_completes_and_cannot_replay_code() -> None:
    transport = Transport(challenge(), challenge(), authenticated())
    sessions, identifier = begin(transport)
    state = sessions._sessions[identifier]
    sessions.send(OWNER, CONNECTION, identifier, "SMS")
    outcome = sessions.verify(OWNER, CONNECTION, identifier, "123456")
    assert outcome.step == "completed"
    assert outcome.session is not None
    assert transport.calls[-1].get("otp") == "123456"
    assert state.password == ""
    assert state.cookies == {}
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.verify(OWNER, CONNECTION, identifier, "123456")


def test_method_outcome_exposes_only_supported_offered_methods() -> None:
    response = challenge()
    response["methods"] = ["SMS", "untrusted", "EMAIL", "SMS"]
    sessions = LoginSessions(transport=Transport(response))
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.step == "method"
    assert outcome.methods == ("SMS", "EMAIL")
    assert outcome.session is None


@pytest.mark.parametrize("method", ["CALL", "email", "invalid"])
def test_unoffered_method_is_rejected_without_provider_request(method: str) -> None:
    transport = Transport(challenge())
    sessions, identifier = begin(transport)
    with pytest.raises(FoodoraError, match="login_flow_incomplete"):
        sessions.send(OWNER, CONNECTION, identifier, method)
    assert len(transport.calls) == 1


def test_verify_before_method_selection_is_rejected() -> None:
    transport = Transport(challenge())
    sessions, identifier = begin(transport)
    with pytest.raises(FoodoraError, match="login_flow_incomplete"):
        sessions.verify(OWNER, CONNECTION, identifier, "123456")
    assert len(transport.calls) == 1


def test_replayed_send_does_not_send_another_code() -> None:
    transport = Transport(challenge(), challenge())
    sessions, identifier = begin(transport)
    sessions.send(OWNER, CONNECTION, identifier, "SMS")
    with pytest.raises(FoodoraError, match="login_flow_incomplete"):
        sessions.send(OWNER, CONNECTION, identifier, "SMS")
    assert len(transport.calls) == 2


@pytest.mark.parametrize("owner,connection", [(UUID(int=9), CONNECTION), (OWNER, UUID(int=9))])
def test_flow_is_bound_to_owner_and_connection(owner: UUID, connection: UUID) -> None:
    transport = Transport(challenge())
    sessions, identifier = begin(transport)
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.send(owner, connection, identifier, "SMS")
    assert len(transport.calls) == 1


def test_expired_flow_is_cleared_before_request() -> None:
    clock = Clock()
    transport = Transport(challenge())
    sessions = LoginSessions(transport=transport, clock=clock, ttl=10)
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.flow_id is not None
    state = sessions._sessions[outcome.flow_id]
    clock.now = 10
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.send(OWNER, CONNECTION, outcome.flow_id, "SMS")
    assert state.password == ""
    assert state.cookies == {}
    assert len(transport.calls) == 1


def test_slow_provider_response_cannot_revive_expired_flow() -> None:
    clock = Clock()

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        clock.now = 11
        return challenge()

    sessions = LoginSessions(transport=transport, clock=clock, ttl=10)
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert sessions._sessions == {}


def test_capacity_and_duplicate_connection_reject_before_password_submission() -> None:
    transport = Transport(challenge())
    sessions = LoginSessions(transport=transport, maximum=1)
    sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    for connection in (CONNECTION, uuid4()):
        with pytest.raises(FoodoraError, match="login_busy"):
            sessions.start(OWNER, connection, "me@example.com", PASSWORD)
    assert len(transport.calls) == 1


def test_otp_rejected_allows_only_three_verification_attempts() -> None:
    transport = Transport(
        challenge(), challenge(), *(FoodoraError("otp_rejected") for _ in range(3))
    )
    sessions, identifier = begin(transport)
    sessions.send(OWNER, CONNECTION, identifier, "SMS")
    state = sessions._sessions[identifier]
    for _ in range(3):
        with pytest.raises(FoodoraError, match="otp_rejected"):
            sessions.verify(OWNER, CONNECTION, identifier, "123456")
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.verify(OWNER, CONNECTION, identifier, "123456")
    assert len(transport.calls) == 5
    assert state.password == ""
    assert state.cookies == {}


def test_valid_code_can_complete_after_rejected_code() -> None:
    transport = Transport(challenge(), challenge(), FoodoraError("otp_rejected"), authenticated())
    sessions, identifier = begin(transport)
    sessions.send(OWNER, CONNECTION, identifier, "SMS")
    with pytest.raises(FoodoraError, match="otp_rejected"):
        sessions.verify(OWNER, CONNECTION, identifier, "bad-code")
    assert sessions.verify(OWNER, CONNECTION, identifier, "good-code").step == "completed"


def test_sending_code_does_not_extend_flow_expiry() -> None:
    clock = Clock()
    transport = Transport(challenge(), challenge())
    sessions = LoginSessions(transport=transport, clock=clock, ttl=10)
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.flow_id is not None
    clock.now = 9
    sessions.send(OWNER, CONNECTION, outcome.flow_id, "SMS")
    clock.now = 10
    with pytest.raises(FoodoraError, match="login_expired"):
        sessions.verify(OWNER, CONNECTION, outcome.flow_id, "123456")
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        {"status": "otp_required", "methods": [], "cookies": COOKIES},
        {"status": "otp_required", "methods": ["SMS"], "cookies": "invalid"},
        {"session": authenticated().get("session"), "report": {"authenticated": False}},
    ],
)
def test_invalid_provider_response_cannot_create_continuation(response: dict[str, Any]) -> None:
    sessions = LoginSessions(transport=Transport(response))
    with pytest.raises(FoodoraError, match="provider_response_invalid"):
        sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert sessions._sessions == {}


def test_transport_request_secret_references_are_cleared_after_completion() -> None:
    requests: list[dict[str, Any]] = []

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        requests.append(payload)
        return authenticated()

    sessions = LoginSessions(transport=transport)
    sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert requests == [{}]


@pytest.mark.parametrize("operation", ["cancel", "cancel_connection", "cleanup", "close"])
def test_discard_clears_sensitive_continuation_state(operation: str) -> None:
    clock = Clock()
    sessions = LoginSessions(transport=Transport(challenge()), clock=clock)
    outcome = sessions.start(OWNER, CONNECTION, "me@example.com", PASSWORD)
    assert outcome.flow_id is not None
    state = sessions._sessions[outcome.flow_id]
    if operation == "cancel":
        sessions.cancel(OWNER, CONNECTION, outcome.flow_id)
    elif operation == "cancel_connection":
        sessions.cancel_connection(OWNER, CONNECTION)
    elif operation == "cleanup":
        clock.now = 601
        sessions.cleanup()
    else:
        sessions.close()
    assert sessions._sessions == {}
    assert state.password == ""
    assert state.cookies == {}
    assert PASSWORD not in repr(state)


def test_unknown_transport_error_is_sanitized_and_flow_discarded() -> None:
    transport = Transport(challenge(), RuntimeError("private-password private-cookie"))
    sessions, identifier = begin(transport)
    with pytest.raises(FoodoraError, match="^provider_unavailable$"):
        sessions.send(OWNER, CONNECTION, identifier, "SMS")
    assert sessions._sessions == {}


def test_concurrent_request_does_not_submit_another_code() -> None:
    entered, release = Event(), Event()
    calls = 0

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(5)
        return challenge()

    sessions, identifier = begin(transport)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(sessions.send, OWNER, CONNECTION, identifier, "SMS")
        assert entered.wait(5)
        try:
            with pytest.raises(FoodoraError, match="login_busy"):
                sessions.send(OWNER, CONNECTION, identifier, "SMS")
        finally:
            release.set()
        assert pending.result().step == "otp"
    assert calls == 2


def test_cancel_inflight_request_invalidates_its_eventual_result() -> None:
    entered, release = Event(), Event()
    calls = 0

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(5)
            return authenticated()
        return challenge()

    sessions, identifier = begin(transport)
    state = sessions._sessions[identifier]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(sessions.send, OWNER, CONNECTION, identifier, "SMS")
        assert entered.wait(5)
        try:
            sessions.cancel(OWNER, CONNECTION, identifier)
            assert state.password == ""
            assert state.cookies == {}
        finally:
            release.set()
        with pytest.raises(FoodoraError, match="login_expired"):
            pending.result()
    assert sessions._sessions == {}
