"""Browser-initialized Lidl login; HTTP submits credentials and verification codes."""

from collections.abc import Callable
from copy import copy
from http.cookiejar import CookieJar
from urllib.parse import urldefrag

import httpx

from .device import BrowserSession, bootstrap_browser, chrome_headers, chrome_request
from .login_protocol import HttpLogin, LoginError, Status, _trusted


class LidlLogin(HttpLogin):
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
        self._http.event_hooks["request"].append(chrome_request)

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
        self._http.headers.update(chrome_headers(snapshot.user_agent))
        self._captcha_token = snapshot.captcha_token
        self._browser_device_id = snapshot.device_id
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
        return target, values
