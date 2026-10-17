import html
import json
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from http_login.client import HttpLogin, LoginError, LoginPage


def test_code_exchange_hands_off_original_refresh_without_diagnostic_rotation() -> None:
    grants: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        grant = parse_qs(request.content.decode())["grant_type"][0]
        grants.append(grant)
        if grant == "authorization_code":
            return httpx.Response(200, json={"refresh_token": "private-original"})
        return httpx.Response(400, json={"error": "invalid_grant"})

    with (
        httpx.Client(transport=httpx.MockTransport(upstream)) as http,
        httpx.Client(transport=httpx.MockTransport(upstream)) as plain,
    ):
        login = HttpLogin(http=http)
        with patch("http_login.client.httpx.Client", return_value=plain):
            status = login._finish(
                f"com.lidlplus.app://callback?code=once&state={login._state}"
            )
        assert status.step == "completed"
        assert grants == ["authorization_code"]
        assert login.take_refresh_token() == "private-original"
        with pytest.raises(LoginError, match="token_already_consumed"):
            login.take_refresh_token()


def test_errors_are_values_and_may_be_separate_from_model() -> None:
    initial = LoginPage(
        page({"TwoFactorRequired": False})
        + '<div app-errors="{&quot;error&quot;:&quot;&quot;}"></div>'
    )
    assert initial.errors == []
    failure = LoginPage(
        page({"TwoFactorRequired": False})
        + '<div app-errors="{&quot;error&quot;:&quot;'
        'InvalidDeviceIdError&quot;}"></div>'
    )
    assert failure.errors == ["InvalidDeviceIdError"]


def test_initial_default_otp_method_does_not_skip_password() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=page({"TwoFactorRequired": False, "OtpMethod": "Email"})
            )
        )
    ) as http:
        assert HttpLogin(http=http).begin().step == "password"


def test_captcha_field_error_is_not_reported_as_wrong_credentials() -> None:
    body = page({"CountryCode": "CZ"}).replace(
        'app-errors="{}"',
        'app-errors="{&quot;ReCaptchaToken&quot;:&quot;'
        'localized rejection private-data&quot;}"',
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=body if request.method == "POST" else page({"CountryCode": "CZ"})
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match="^captcha_required$"):
            login.credentials("owner@example.com", "private-password")
        assert "private-data" not in repr(login.diagnostics)


def test_unknown_provider_error_does_not_assert_bad_credentials() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=page(
                {"CountryCode": "CZ"}, errors=["private unknown provider message"]
            )
            if request.method == "POST"
            else page({"CountryCode": "CZ"}),
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match="^login_rejected$"):
            login.credentials("owner@example.com", "private-password")
        assert "private" not in repr(login.diagnostics)


@pytest.mark.parametrize("count,expected", [(3, 3), ("3", -1), (True, -1)])
def test_over_capacity_reports_only_integer_failed_count(
    count: object, expected: int
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        return httpx.Response(
            200,
            text=page({"AccessFailedCount": count}, errors=["OverCapacityError"]),
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match="^provider_busy$"):
            login.credentials("owner@example.com", "private-password")
        assert login.diagnostics.get("access_failed_count") == expected


def test_password_post_contains_only_fields_sent_by_official_form() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                text=page(
                    {
                        "CountryCode": "CZ",
                        "TwoFactorRequired": False,
                        "HasRedirection": False,
                        "AccessFailedCount": 0,
                    }
                ),
            )
        data = parse_qs(request.content.decode(), keep_blank_values=True)
        assert set(data) == {
            "__RequestVerificationToken",
            "Email",
            "PhoneNumber",
            "PhoneCountryCode",
            "Password",
            "RememberMe",
            "Step",
            "DeviceId",
        }
        return httpx.Response(
            200, text=page({"TwoFactorRequired": True, "OtpMethod": "Email"})
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HttpLogin(http=http)
        login.begin()
        assert login.credentials("owner@example.com", "private-password").step == "otp"


def page(model: dict[str, object], *, errors: list[str] | None = None) -> str:
    return (
        '<form id="login-form" method="post">'
        '<input type="hidden" name="__RequestVerificationToken" value="csrf">'
        '</form><app-login app-model="'
        + html.escape(json.dumps(model), quote=True)
        + '" app-errors="'
        + html.escape(
            json.dumps({str(i): error for i, error in enumerate(errors or [])}),
            quote=True,
        )
        + '"></app-login>'
    )


def test_email_password_mfa_callback_and_token_exchange() -> None:
    calls: list[tuple[str, str]] = []
    state = ""

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal state
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("authorize"):
            state = request.url.params.get("state", "")
            return httpx.Response(
                302, headers={"Location": "/Account/Login?returnUrl=internal"}
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                text=page(
                    {"CountryCode": "CZ", "Step": None, "TwoFactorRequired": False}
                ),
                headers={"Set-Cookie": "session=one; Path=/; Secure"},
            )
        data = parse_qs(request.content.decode(), keep_blank_values=True)
        if request.url.path == "/Account/Login":
            assert data.get("Email") == ["owner@example.com"]
            assert data.get("PhoneNumber") == [""]
            assert data.get("Password") == ["private-password"]
            assert data.get("Step") == ["1"]
            assert data.get("__RequestVerificationToken") == ["csrf"]
            assert "session=one" in request.headers.get("Cookie", "")
            return httpx.Response(
                200,
                text=page(
                    {
                        "CountryCode": "CZ",
                        "TwoFactorRequired": True,
                        "OtpMethod": "Email",
                        "Token": "intermediate",
                    }
                ),
            )
        if request.url.path == "/Account/Login/Otp":
            assert data.get("VerificationCode") == ["123456"]
            assert data.get("Method") == ["Email"]
            assert data.get("Token") == ["intermediate"]
            assert "Password" not in data
            return httpx.Response(
                302,
                headers={
                    "Location": f"com.lidlplus.app://callback?code=once&state={state}"
                },
            )
        assert request.url.path == "/connect/token"
        assert data.get("code") == ["once"] and data.get("code_verifier")
        return httpx.Response(
            200,
            json={
                "refresh_token": "private-refresh",
                "access_token": "private-access",
                "expires_in": 3600,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        login = HttpLogin(http=http)
        assert login.begin().step == "password"
        assert login.credentials("owner@example.com", "private-password").step == "otp"
        assert login.verify("123456").step == "completed"
        assert login.diagnostics.get("token_fields") == (
            "access_token,expires_in,refresh_token"
        )
        assert not any(key.startswith("refresh_") for key in login.diagnostics)
        assert login.take_refresh_token() == "private-refresh"
        with pytest.raises(LoginError, match="token_already_consumed"):
            login.take_refresh_token()
        with pytest.raises(LoginError, match="login_not_active"):
            login.verify("123456")
        assert "private-password" not in repr(login.__dict__)
        assert "private-refresh" not in repr(login.__dict__)
    assert [path for _, path in calls].count("/connect/token") == 1


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        "http://accounts.lidl.com/steal",
        "https://accounts.lidl.com.evil.example/steal",
        "https://accounts.lidl.com:444/steal",
    ],
)
def test_external_redirect_never_receives_credentials_or_cookies(location: str) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": location})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(LoginError, match="unexpected_login_origin"),
    ):
        HttpLogin(http=http).begin()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error,expected",
    [
        ("InvalidDeviceIdError", "device_verification_required"),
        ("CaptchaError", "captcha_required"),
        ("InvalidPasswordError", "credentials_rejected"),
    ],
)
def test_provider_errors_are_classified_without_returning_private_payload(
    error: str, expected: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        return httpx.Response(
            200, text=page({"CountryCode": "CZ"}, errors=[error, "private-password"])
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match=f"^{expected}$"):
            login.credentials("owner@example.com", "private-password")
        assert "private-password" not in repr(login.__dict__)


def test_uncertain_credential_post_is_not_replayed() -> None:
    posts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        posts.append(request.url.path)
        raise httpx.ReadTimeout("private-password")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        login = HttpLogin(http=http)
        login.begin()
        for expected in ["login_request_uncertain", "login_not_active"]:
            with pytest.raises(LoginError, match=f"^{expected}$"):
                login.credentials("owner@example.com", "private-password")
    assert posts == ["/Account/Login"]


def test_wrong_callback_state_never_exchanges_code() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        assert request.url.path == "/Account/Login"
        return httpx.Response(
            302,
            headers={
                "Location": "com.lidlplus.app://callback?code=private-code&state=wrong"
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        login = HttpLogin(http=http)
        login.begin()
        with pytest.raises(LoginError, match="callback_invalid"):
            login.credentials("owner@example.com", "private-password")
