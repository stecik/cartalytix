"""HTTP-only CZ login probe; no persistence, POST retry or debug bodies."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

AUTH = "https://accounts.lidl.com"
CLIENT_ID = "LidlPlusNativeClient"
CALLBACK = "com.lidlplus.app://callback"


class LoginError(RuntimeError):
    """Only fixed diagnostic codes may leave the provider boundary."""


@dataclass(frozen=True)
class Status:
    step: Literal["password", "otp", "completed"]
    method: str | None = None


class LoginPage(HTMLParser):
    def __init__(self, body: str) -> None:
        super().__init__(convert_charrefs=True)
        self.model: dict[str, Any] = {}
        self.hidden: dict[str, str] = {}
        self.action = ""
        self.errors: list[str] = []
        self.error_fields: set[str] = set()
        self._form = False
        self.feed(body)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._form = values.get("id") == "login-form"
            if self._form:
                self.action = values.get("action") or ""
        if tag == "input" and self._form and values.get("name"):
            self.hidden[str(values.get("name"))] = values.get("value") or ""
        if "app-model" in values:
            try:
                model = json.loads(values.get("app-model") or "{}")
            except ValueError:
                raise LoginError("login_markup_changed") from None
            if not isinstance(model, dict):
                raise LoginError("login_markup_changed")
            self.model = {
                key: value
                for key, value in model.items()
                if "password" not in key.lower()
            }
        if "app-errors" in values:
            try:
                errors = json.loads(values.get("app-errors") or "{}")
            except ValueError:
                raise LoginError("login_markup_changed") from None
            if not isinstance(errors, dict):
                raise LoginError("login_markup_changed")
            self.errors.extend(str(error) for error in errors.values() if error)
            self.error_fields.update(
                key
                for key, value in errors.items()
                if value
                and key
                in {
                    "Email",
                    "PhoneNumber",
                    "Password",
                    "DeviceId",
                    "ReCaptchaToken",
                    "VerificationCode",
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = False


def _trusted(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port) != (
            "https",
            "accounts.lidl.com",
            None,
        ):
            raise ValueError
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError
    except ValueError:
        raise LoginError("unexpected_login_origin") from None
    return url


class HttpLogin:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self._owned = http is None
        self._http = (
            http
            if http is not None
            else httpx.Client(timeout=25, follow_redirects=False)
        )
        self._verifier = secrets.token_urlsafe(64)
        self._state = secrets.token_urlsafe(32)
        self._phase = "new"
        self._page: LoginPage | None = None
        self._url = ""
        self._refresh: str | None = None
        self.diagnostics: dict[str, int | bool | str] = {"requests": 0}
        self._http.headers.update(
            {
                "Accept-Language": "cs-CZ",
                "User-Agent": "Cartalytix-HTTP-login-experiment/1",
            }
        )

    def close(self) -> None:
        self._phase = "closed"
        self._refresh = None
        self._page = None
        self._verifier = self._state = self._url = ""
        self._http.cookies.clear()
        if self._owned:
            self._http.close()

    def begin(self) -> Status:
        return self._advance("GET", self._authorization_url())

    def _authorization_url(self) -> str:
        if self._phase != "new":
            raise LoginError("login_not_active")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        query = urlencode(
            {
                "client_id": CLIENT_ID,
                "redirect_uri": CALLBACK,
                "response_type": "code",
                "scope": "openid profile offline_access lpprofile lpapis",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "Country": "CZ",
                "language": "cs-CZ",
                "state": self._state,
                "nonce": secrets.token_urlsafe(32),
            }
        )
        return f"{AUTH}/connect/authorize?{query}"

    def _device_mark(self) -> str:
        values = {
            cookie.value
            for cookie in self._http.cookies.jar
            if cookie.name == "lidl-sso-browser-mark"
            and cookie.domain.lstrip(".") in {"accounts.lidl.com", "lidl.com"}
        }
        if len(values) > 1:
            raise LoginError("device_cookie_ambiguous")
        return next(iter(values), "") or ""

    def credentials(self, email: str, password: str) -> Status:
        if self._phase != "password":
            raise LoginError("login_not_active")
        if not email or "@" not in email or not password:
            raise LoginError("email_and_password_required")
        target, model_values = self._form("/Account/Login")
        values = {
            key: value
            for key, value in model_values.items()
            if key
            in {
                "DeviceId",
                "Email",
                "Password",
                "PhoneCountryCode",
                "PhoneNumber",
                "ReCaptchaToken",
                "RememberMe",
                "Step",
                "__RequestVerificationToken",
            }
        }
        values.update(
            {
                "Email": email.strip(),
                "PhoneNumber": "",
                "PhoneCountryCode": "",
                "Password": password,
                "RememberMe": "false",
                "Step": "1",
                "DeviceId": self._form_device_id(),
            }
        )
        return self._advance("POST", target, values)

    def _form_device_id(self) -> str:
        return unquote(self._device_mark())

    def verify(self, code: str) -> Status:
        if self._phase != "otp":
            raise LoginError("login_not_active")
        target, values = self._form("/Account/Login/Otp")
        method = values.get("OtpMethod") or "Email"
        values.update(
            {"VerificationCode": code.strip(), "Method": method, "OtpMethod": method}
        )
        return self._advance("POST", target, values)

    def take_refresh_token(self) -> str:
        if self._refresh is None:
            raise LoginError("token_already_consumed")
        value, self._refresh = self._refresh, None
        return value

    def _form(self, path: str) -> tuple[str, dict[str, str]]:
        if self._page is None or not self._page.hidden.get(
            "__RequestVerificationToken"
        ):
            raise LoginError("login_markup_changed")
        parsed = urlsplit(self._url)
        target = (
            urljoin(self._url, self._page.action)
            if self._page.action
            else urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
        )
        values = dict(self._page.hidden)
        for key, value in self._page.model.items():
            if isinstance(value, bool):
                values[key] = str(value).lower()
            elif isinstance(value, str | int | float):
                values[key] = str(value)
        values.pop("Password", None)
        return _trusted(target), values

    def _advance(
        self, method: str, url: str, values: dict[str, str] | None = None
    ) -> Status:
        before = self._phase
        self._phase = "failed"
        for _ in range(15):
            _trusted(url)
            try:
                response = self._http.request(
                    method,
                    url,
                    data=values,
                    headers={"Referer": self._url} if self._url else {},
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                raise LoginError(
                    "login_request_uncertain"
                    if method == "POST"
                    else "login_unavailable"
                ) from None
            self.diagnostics.update(
                {
                    "requests": int(self.diagnostics.get("requests", 0)) + 1,
                    "http_status": response.status_code,
                    "has_device_mark": bool(self._device_mark()),
                }
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = urljoin(url, response.headers.get("Location", ""))
                if location.startswith("com.lidlplus.app:"):
                    return self._finish(location)
                _trusted(location)
                if method == "POST" and response.status_code in {307, 308}:
                    raise LoginError("credential_redirect_rejected")
                url, method, values = location, "GET", None
                continue
            if response.status_code != 200:
                raise LoginError(
                    "login_rate_limited"
                    if response.status_code == 429
                    else "login_http_error"
                )
            return self._accept(LoginPage(response.text), url, before)
        raise LoginError("login_redirect_loop")

    def _accept(self, page: LoginPage, url: str, before: str) -> Status:
        self._phase = "failed"
        errors = " ".join(page.errors).lower()
        failed_count = page.model.get("AccessFailedCount")
        self.diagnostics.update(
            {
                "provider_reported_errors": bool(errors),
                "captcha_field_error": "ReCaptchaToken" in page.error_fields,
                "password_field_error": "Password" in page.error_fields,
                "access_failed_count": failed_count
                if isinstance(failed_count, int) and not isinstance(failed_count, bool)
                else -1,
            }
        )
        if "device" in errors or "DeviceId" in page.error_fields:
            raise LoginError("device_verification_required")
        if "ReCaptchaToken" in page.error_fields or any(
            word in errors for word in ["captcha", "turnstile"]
        ):
            raise LoginError("captcha_required")
        if "accountlockedout" in errors:
            raise LoginError("account_locked")
        if "overcapacityerror" in errors:
            raise LoginError("provider_busy")
        if errors:
            if before == "otp":
                raise LoginError("otp_rejected")
            if page.error_fields & {"Email", "PhoneNumber", "Password"} or any(
                code in errors
                for code in ["invalidpassworderror", "invalidcredentialserror"]
            ):
                raise LoginError("credentials_rejected")
            raise LoginError("login_rejected")
        if not page.model or not page.hidden.get("__RequestVerificationToken"):
            raise LoginError("login_markup_changed")
        self._page, self._url = page, url
        if page.model.get("TwoFactorRequired") is True or (
            before != "new" and page.model.get("OtpMethod")
        ):
            if before == "otp":
                raise LoginError("otp_rejected")
            self._phase = "otp"
            return Status("otp", str(page.model.get("OtpMethod") or "Email"))
        if before != "new":
            raise LoginError("login_flow_incomplete")
        self._phase = "password"
        return Status("password")

    def _finish(self, url: str) -> Status:
        parsed = urlsplit(url)
        values = parse_qs(parsed.query, keep_blank_values=True)
        states, codes = values.get("state", []), values.get("code", [])
        if (
            (parsed.scheme, parsed.netloc, parsed.path)
            != ("com.lidlplus.app", "callback", "")
            or parsed.fragment
            or "error" in values
            or len(states) != 1
            or len(codes) != 1
            or not codes[0]
            or not hmac.compare_digest(states[0].encode(), self._state.encode())
        ):
            raise LoginError("callback_invalid")
        try:
            response = self._http.post(
                f"{AUTH}/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "code": codes[0],
                    "redirect_uri": CALLBACK,
                    "code_verifier": self._verifier,
                },
                auth=(CLIENT_ID, "secret"),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise LoginError("token_exchange_uncertain") from None
        if response.status_code != 200:
            raise LoginError("token_exchange_failed")
        try:
            payload = response.json()
            refresh = payload.get("refresh_token")
            if not isinstance(refresh, str) or not refresh:
                raise ValueError
        except (ValueError, AttributeError):
            raise LoginError("token_response_invalid") from None
        self.diagnostics["token_fields"] = ",".join(
            sorted(key for key in payload if re.fullmatch(r"[a-z_]{1,64}", str(key)))
        )
        scope = payload.get("scope")
        self.diagnostics["token_scope"] = (
            scope
            if isinstance(scope, str) and re.fullmatch(r"[\w .:-]{0,512}", scope)
            else ""
        )
        # The backend owns validation and rotation after the once-only handoff.
        self._refresh = refresh
        self._phase = "completed"
        self._page = None
        return Status("completed")
