"""OAuth protocol tests use only synthetic credentials and in-memory HTTP."""

import html
import json
from urllib.parse import parse_qs

import httpx
import pytest

from cartalytix.providers.lidl.login_protocol import HttpLogin, LoginError


def page(model: dict[str, object], errors: dict[str, str] | None = None) -> str:
    return (
        '<form id="login-form"><input name="__RequestVerificationToken" value="csrf"></form>'
        f'<div app-model="{html.escape(json.dumps(model), quote=True)}" '
        f'app-errors="{html.escape(json.dumps(errors or {}), quote=True)}"></div>'
    )


def test_password_mfa_and_code_exchange_handoff_token_only_once() -> None:
    state = ""
    grants: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal state
        if request.method == "GET":
            state = request.url.params.get("state", "")
            assert request.url.params.get("code_challenge_method") == "S256"
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        values = parse_qs(request.content.decode(), keep_blank_values=True)
        if request.url.path == "/Account/Login":
            assert values.get("Password") == ["private-password"]
            assert values.get("Step") == ["1"]
            assert values.get("__RequestVerificationToken") == ["csrf"]
            assert "CountryCode" not in values
            return httpx.Response(200, text=page({"TwoFactorRequired": True, "OtpMethod": "Email"}))
        if request.url.path == "/Account/Login/Otp":
            assert values.get("VerificationCode") == ["123456"]
            assert "Password" not in values
            return httpx.Response(
                302, headers={"Location": f"com.lidlplus.app://callback?code=once&state={state}"}
            )
        assert request.url.path == "/connect/token"
        grants.append(values.get("grant_type", [""])[0])
        assert values.get("code") == ["once"] and values.get("code_verifier")
        return httpx.Response(200, json={"refresh_token": "private-refresh"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        login = HttpLogin(http=http)
        assert login.begin().step == "password"
        assert login.credentials("owner@example.com", "private-password").step == "otp"
        assert login.verify("123456").step == "completed"
        assert login.take_refresh_token() == "private-refresh"
        with pytest.raises(LoginError, match="token_already_consumed"):
            login.take_refresh_token()
        login.close()
        assert "private" not in repr(login.__dict__)
    assert grants == ["authorization_code"]


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/steal",
        "http://accounts.lidl.com/steal",
        "https://accounts.lidl.com:444/steal",
        "https://user:secret@accounts.lidl.com/steal",
    ],
)
def test_external_redirect_stops_before_credentials_leave_origin(target: str) -> None:
    calls: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": target})

    with (
        httpx.Client(transport=httpx.MockTransport(provider)) as http,
        pytest.raises(LoginError, match="unexpected_login_origin"),
    ):
        HttpLogin(http=http).begin()
    assert calls == ["accounts.lidl.com"]


@pytest.mark.parametrize(
    "field,message,expected",
    [
        ("Password", "private-provider-text", "credentials_rejected"),
        ("ReCaptchaToken", "private-provider-text", "captcha_required"),
        ("DeviceId", "private-provider-text", "device_verification_required"),
        ("error", "OverCapacityError", "provider_busy"),
        ("error", "private-provider-text", "login_rejected"),
    ],
)
def test_provider_errors_are_fixed_codes(field: str, message: str, expected: str) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=page(
                {"CountryCode": "CZ"}, {field: message} if request.method == "POST" else None
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match=f"^{expected}$"):
            login.credentials("owner@example.com", "private-password")


def test_uncertain_password_post_is_never_replayed() -> None:
    posts: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        posts.append(request.url.path)
        raise httpx.ReadTimeout("private-password")

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        login = HttpLogin(http=http)
        login.begin()
        for code in ["login_request_uncertain", "login_not_active"]:
            with pytest.raises(LoginError, match=f"^{code}$"):
                login.credentials("owner@example.com", "private-password")
    assert posts == ["/Account/Login"]


@pytest.mark.parametrize("status", [302, 307, 308])
def test_callback_state_and_post_redirects_are_checked(status: int) -> None:
    calls: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        location = (
            "com.lidlplus.app://callback?code=once&state=wrong"
            if status == 302
            else "/Account/Login"
        )
        return httpx.Response(status, headers={"Location": location})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match="callback_invalid|credential_redirect_rejected"):
            login.credentials("owner@example.com", "private-password")
    assert calls == ["/connect/authorize", "/Account/Login"]
