"""Throwaway browser-assisted initialization; credentials stay in HTTP code."""

from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urldefrag

import httpx

from .client import AUTH, CLIENT_ID, HttpLogin, LoginError, Status, _trusted

browser_slot = BoundedSemaphore(1)


@dataclass(repr=False)
class BrowserSession:
    url: str
    user_agent: str
    cookies: CookieJar
    captcha_token: str
    device_id: str = ""


def browser_cookie(raw: Mapping[str, Any]) -> Cookie:
    domain = str(raw.get("domain", ""))
    expires = float(raw.get("expires", -1))
    return Cookie(
        version=0,
        name=str(raw.get("name", "")),
        value=str(raw.get("value", "")),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."),
        path=str(raw.get("path", "/")),
        path_specified=True,
        secure=bool(raw.get("secure")),
        expires=int(expires) if expires > 0 else None,
        discard=expires <= 0,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if raw.get("httpOnly") else {},
        rfc2109=False,
    )


def bootstrap_browser(
    url: str, cookies: CookieJar | None, user_agent: str | None
) -> BrowserSession:
    _trusted(url)
    if not browser_slot.acquire(timeout=1):
        raise LoginError("device_bootstrap_busy")
    try:
        from playwright._impl._api_structures import SetCookieParam
        from playwright.sync_api import Error, Route, TimeoutError, sync_playwright

        stage = "device"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chromium", headless=True)
                try:
                    context = browser.new_context(
                        locale="cs-CZ", accept_downloads=False, user_agent=user_agent
                    )
                    if cookies is not None:
                        context.add_cookies(
                            [
                                SetCookieParam(
                                    name=cookie.name,
                                    value=cookie.value or "",
                                    domain=cookie.domain,
                                    path=cookie.path,
                                    secure=cookie.secure,
                                    httpOnly=cookie.has_nonstandard_attr("HttpOnly"),
                                    expires=float(cookie.expires)
                                    if cookie.expires is not None
                                    else -1,
                                )
                                for cookie in cookies
                                if cookie.domain.lstrip(".")
                                in {"accounts.lidl.com", "lidl.com"}
                            ]
                        )
                    page = context.new_page()

                    def navigation(route: Route) -> None:
                        request = route.request
                        if (
                            request.is_navigation_request()
                            and request.frame == page.main_frame
                        ):
                            try:
                                _trusted(request.url)
                            except LoginError:
                                route.abort()
                                return
                        route.continue_()

                    page.route("**/*", navigation)
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=30000
                    )
                    if response is None or response.status != 200:
                        raise LoginError("device_bootstrap_http_error")
                    page.wait_for_function(
                        "document.cookie.split(';').some(c => "
                        "c.trim().startsWith('lidl-sso-browser-mark=') && "
                        "c.trim().length > 'lidl-sso-browser-mark='.length)",
                        timeout=15000,
                    )
                    stage = "captcha"
                    rules_response = context.request.get(
                        f"{AUTH}/api/rules?client_id={CLIENT_ID}&country_code=CZ"
                    )
                    try:
                        rules = rules_response.json()
                        key = rules.get("reCaptchaConfig", {}).get("key")
                    except (ValueError, AttributeError):
                        raise LoginError("captcha_configuration_unavailable") from None
                    if (
                        rules_response.status != 200
                        or not isinstance(key, str)
                        or not key
                    ):
                        raise LoginError("captcha_configuration_unavailable")
                    stage = "captcha_script"
                    page.wait_for_function(
                        "typeof window.grecaptcha?.enterprise?.execute === 'function'",
                        timeout=15000,
                    )
                    stage = "captcha_execute"
                    captcha = page.evaluate(
                        """async key => await Promise.race([
                          new Promise((resolve,reject) => {
                            window.grecaptcha.enterprise.ready(() => {
                              try {
                                window.grecaptcha.enterprise
                                  .execute(key,{action:'LOGIN'}).then(resolve,reject)
                              } catch(error) { reject(error) }
                            })
                          }),
                          new Promise((resolve,reject) => setTimeout(
                            () => reject(new Error('captcha_timeout')),15000
                          ))
                        ])""",
                        key,
                    )
                    if not isinstance(captcha, str) or not captcha:
                        raise LoginError("captcha_token_unavailable")
                    stage = "device_form"
                    page.wait_for_function(
                        "Boolean(document.querySelector("
                        "'form#login-form input[name=\"DeviceId\"]')?.value)",
                        timeout=15000,
                    )
                    device_id = page.locator(
                        'form#login-form input[name="DeviceId"]'
                    ).input_value()
                    jar = CookieJar()
                    for raw in context.cookies():
                        if raw.get("domain", "").lstrip(".") in {
                            "accounts.lidl.com",
                            "lidl.com",
                        }:
                            jar.set_cookie(browser_cookie(raw))
                    return BrowserSession(
                        url=_trusted(urldefrag(page.url).url),
                        user_agent=str(page.evaluate("navigator.userAgent")),
                        cookies=jar,
                        captcha_token=captcha,
                        device_id=device_id,
                    )
                finally:
                    browser.close()
        except Error as error:
            message = str(error).lower()
            if stage == "captcha_execute":
                code = "captcha_execution_failed"
                if (
                    "invalid recaptcha client" in message
                    or "invalid site key" in message
                    or "no recaptcha clients" in message
                ):
                    code = "captcha_invalid_client"
                elif "captcha_timeout" in message:
                    code = "captcha_execution_timeout"
                elif (
                    "networkerror" in message
                    or "err_network" in message
                    or "failed to fetch" in message
                ):
                    code = "captcha_network_error"
            elif stage == "captcha_script":
                code = (
                    "captcha_script_timeout"
                    if isinstance(error, TimeoutError)
                    else "captcha_script_error"
                )
            elif stage == "captcha":
                code = "captcha_configuration_unavailable"
            else:
                code = "device_bootstrap_unavailable"
            raise LoginError(code) from None
    except ImportError:
        raise LoginError("device_bootstrap_runtime_missing") from None
    finally:
        browser_slot.release()


class HybridLogin(HttpLogin):
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        bootstrap: Callable[
            [str, CookieJar | None, str | None], BrowserSession
        ] = bootstrap_browser,
    ) -> None:
        super().__init__(http=http)
        self._bootstrap = bootstrap
        self._captcha_token = ""
        self._browser_url = ""
        self._browser_device_id = ""

    def close(self) -> None:
        self._captcha_token = self._browser_url = ""
        self._browser_device_id = ""
        super().close()

    def begin(self) -> Status:
        url = self._authorization_url()
        self._phase = "failed"
        snapshot = self._bootstrap(url, None, None)
        target = self._apply_browser_session(snapshot)
        self._browser_url = target
        self._phase = "new"
        return self._advance("GET", target)

    def _apply_browser_session(self, snapshot: BrowserSession) -> str:
        target = _trusted(urldefrag(snapshot.url).url)
        for cookie in snapshot.cookies:
            if cookie.domain.lstrip(".") not in {
                "accounts.lidl.com",
                "lidl.com",
            } or not cookie.path.startswith("/"):
                raise LoginError("browser_cookie_rejected")
        if not snapshot.captcha_token:
            raise LoginError("captcha_token_unavailable")
        if not snapshot.device_id or snapshot.device_id in {"undefined", "null"}:
            raise LoginError("device_bootstrap_missing_id")
        self._http.cookies.clear()
        for cookie in snapshot.cookies:
            self._http.cookies.jar.set_cookie(copy(cookie))
        if not self._device_mark():
            raise LoginError("device_bootstrap_missing_mark")
        self._http.headers["User-Agent"] = snapshot.user_agent
        self._captcha_token = snapshot.captcha_token
        self._browser_device_id = snapshot.device_id
        self.diagnostics["browser_bootstrap"] = True
        self.diagnostics["captcha_token_acquired"] = True
        self.diagnostics["device_id_from_form"] = True
        return target

    def _form_device_id(self) -> str:
        return self._browser_device_id

    def _form(self, path: str) -> tuple[str, dict[str, str]]:
        if not self._captcha_token:
            try:
                snapshot = self._bootstrap(
                    self._browser_url,
                    self._http.cookies.jar,
                    self._http.headers.get("User-Agent"),
                )
                self._apply_browser_session(snapshot)
            except LoginError:
                self._phase = "failed"
                raise
        target, values = super()._form(path)
        values["ReCaptchaToken"], self._captcha_token = self._captcha_token, ""
        values["DeviceId"] = self._form_device_id()
        self.diagnostics["captcha_attached"] = True
        return target, values
