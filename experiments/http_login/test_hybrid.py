from http.cookiejar import CookieJar
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import httpx
import pytest

from http_login.client import AUTH, LoginError
from http_login.hybrid import BrowserSession, HybridLogin, bootstrap_browser
from http_login.test_client import page


def session(
    *, url: str = f"{AUTH}/Account/Login?returnUrl=internal", mark: bool = True
) -> BrowserSession:
    cookies = httpx.Cookies()
    cookies.set(
        "browser-session", "private-cookie", domain="accounts.lidl.com", path="/Account"
    )
    if mark:
        cookies.set(
            "lidl-sso-browser-mark", "private-mark", domain=".lidl.com", path="/"
        )
    for cookie in cookies.jar:
        cookie.secure = True
    snapshot = BrowserSession(
        url=url,
        user_agent="browser-agent",
        cookies=cookies.jar,
        captcha_token="private-captcha",
    )
    snapshot.device_id = "private-form-device"
    return snapshot


@pytest.mark.parametrize("fragment", ["", "#login"])
def test_browser_session_reaches_http_credentials_with_same_identity(
    fragment: str,
) -> None:
    bootstrap_urls: list[str] = []
    seen: list[str] = []
    state = ""

    def bootstrap(
        url: str, cookies: CookieJar | None, user_agent: str | None
    ) -> BrowserSession:
        nonlocal state
        bootstrap_urls.append(url)
        if len(bootstrap_urls) == 1:
            state = httpx.URL(url).params.get("state", "")
            assert parse_qs(httpx.URL(url).query.decode())["code_challenge_method"] == [
                "S256"
            ]
            assert cookies is None and user_agent is None
        else:
            assert cookies is not None and user_agent == "browser-agent"
        result = session(url=f"{AUTH}/Account/Login?returnUrl=internal{fragment}")
        result.captcha_token = f"private-captcha-{len(bootstrap_urls)}"
        return result

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        assert not request.url.fragment
        assert request.headers.get("User-Agent") == "browser-agent"
        assert "lidl-sso-browser-mark=private-mark" in request.headers.get("Cookie", "")
        if request.url.path.startswith("/Account"):
            assert "browser-session=private-cookie" in request.headers.get("Cookie", "")
        else:
            assert "browser-session=" not in request.headers.get("Cookie", "")
        if request.method == "GET":
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        data = parse_qs(request.content.decode())
        if request.url.path == "/Account/Login/Otp":
            assert data["VerificationCode"] == ["123456"]
            assert data["ReCaptchaToken"] == ["private-captcha-2"]
            assert "Password" not in data
            return httpx.Response(
                302,
                headers={
                    "Location": f"com.lidlplus.app://callback?code=once&state={state}"
                },
            )
        if request.url.path == "/connect/token":
            assert data["code"] == ["once"] and data.get("code_verifier")
            return httpx.Response(200, json={"refresh_token": "private-refresh"})
        assert data["DeviceId"] == ["private-form-device"]
        assert data["Password"] == ["private-password"]
        assert data["ReCaptchaToken"] == ["private-captcha-1"]
        return httpx.Response(
            200, text=page({"TwoFactorRequired": True, "OtpMethod": "Email"})
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HybridLogin(http=http, bootstrap=bootstrap)
        assert login.begin().step == "password"
        assert login.credentials("owner@example.com", "private-password").step == "otp"
        assert login.verify("123456").step == "completed"
        assert login.take_refresh_token() == "private-refresh"
        assert login.diagnostics.get("has_device_mark") is True
        assert "private" not in repr(login.diagnostics)
        login.close()
        assert not list(http.cookies.jar)
    assert len(bootstrap_urls) == 2 and seen == ["GET", "POST", "POST", "POST"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("external", "unexpected_login_origin"),
        ("no-mark", "device_bootstrap_missing_mark"),
        ("foreign-cookie", "browser_cookie_rejected"),
        ("no-captcha", "captcha_token_unavailable"),
        ("no-device-id", "device_bootstrap_missing_id"),
    ],
)
def test_bad_browser_session_stops_before_any_http_request(
    mode: str, expected: str
) -> None:
    def bootstrap(
        url: str, cookies: CookieJar | None, user_agent: str | None
    ) -> BrowserSession:
        result = session(
            url="https://evil.example/"
            if mode == "external"
            else f"{AUTH}/Account/Login",
            mark=mode != "no-mark",
        )
        if mode == "foreign-cookie":
            jar = httpx.Cookies(result.cookies)
            jar.set("secret", "private-cookie", domain="evil.example")
        if mode == "no-captcha":
            result.captcha_token = ""
        if mode == "no-device-id":
            result.device_id = ""
        return result

    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        login = HybridLogin(http=http, bootstrap=bootstrap)
        with pytest.raises(LoginError, match=f"^{expected}$"):
            login.begin()
        with pytest.raises(LoginError, match="login_not_active"):
            login.credentials("owner@example.com", "private-password")
        login.close()
    assert not calls


def test_browser_snapshot_repr_contains_no_cookie_values() -> None:
    snapshot = session()
    assert "private" not in repr(snapshot)
    assert isinstance(snapshot.cookies, CookieJar)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (None, None),
        ("Invalid reCAPTCHA client id: private-value", "captcha_invalid_client"),
        ("NetworkError: private-value", "captcha_network_error"),
        ("captcha_timeout private-value", "captcha_execution_timeout"),
        ("unknown private-value", "captcha_execution_failed"),
    ],
)
def test_browser_export_keeps_account_paths_and_excludes_third_party_cookies(
    failure: str | None, expected: str | None
) -> None:
    raw = [
        {
            "name": "lidl-sso-browser-mark",
            "value": "private-mark",
            "domain": "accounts.lidl.com",
            "path": "/",
            "secure": True,
        },
        {
            "name": "account-session",
            "value": "private-account",
            "domain": "accounts.lidl.com",
            "path": "/Account",
            "secure": True,
        },
        {
            "name": "external",
            "value": "private-third-party",
            "domain": ".google.com",
            "path": "/",
            "secure": True,
        },
    ]

    def cookies(urls: object = None) -> list[dict[str, Any]]:
        if urls is None:
            return raw
        return [
            item
            for item in raw
            if item.get("path") == "/" and item.get("domain") == "accounts.lidl.com"
        ]

    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value
    context.cookies.side_effect = cookies
    context.request.get.return_value.status = 200
    context.request.get.return_value.json.return_value = {
        "reCaptchaConfig": {"key": "public-test-key"}
    }
    page_mock = context.new_page.return_value
    page_mock.url = f"{AUTH}/Account/Login#login"
    page_mock.goto.return_value.status = 200
    page_mock.locator.return_value.input_value.return_value = "private-form-device"
    page_mock.evaluate.side_effect = lambda expression, *args: (
        "browser-agent" if expression == "navigator.userAgent" else "private-captcha"
    )
    with patch("playwright.sync_api.sync_playwright", return_value=manager):
        if failure is not None:
            from playwright.sync_api import Error

            page_mock.evaluate.side_effect = Error(failure)
            with pytest.raises(LoginError, match=f"^{expected}$") as caught:
                bootstrap_browser(f"{AUTH}/Account/Login", None, None)
            assert "private-value" not in str(caught.value)
            browser.close.assert_called_once()
            return
        result = bootstrap_browser(f"{AUTH}/Account/Login", None, None)
    jar = httpx.Cookies(result.cookies)
    assert jar.get("account-session") == "private-account"
    assert jar.get("external") is None
    assert result.captcha_token == "private-captcha"
    assert result.device_id == "private-form-device"
    browser.close.assert_called_once()
