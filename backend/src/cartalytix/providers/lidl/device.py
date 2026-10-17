"""Ephemeral Chrome device verification; no passwords or OTP codes enter the browser."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urldefrag

import httpx

from .login_protocol import AUTH, CLIENT_ID, LoginError, _trusted

browser_slot = BoundedSemaphore(1)
GREASE_CHARS = " (:-./);=?_"
GREASE_VERSIONS = ("8", "99", "24")
GREASE_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def chrome_headers(user_agent: str) -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "cs-CZ,cs;q=0.9",
        "Priority": "u=0, i",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    match = re.search(r"Chrome/(\d+)", user_agent)
    if match is None:
        return headers
    major = int(match.group(1))
    # Chromium's GenerateBrandVersionList GREASE, seeded by the major version.
    grease = (
        f"Not{GREASE_CHARS[major % 11]}A{GREASE_CHARS[(major + 1) % 11]}Brand",
        GREASE_VERSIONS[major % 3],
    )
    brands = [("", "")] * 3
    order = GREASE_ORDERS[major % 6]
    brands[order[0]] = grease
    brands[order[1]] = ("Chromium", str(major))
    brands[order[2]] = ("Google Chrome", str(major))
    if "Windows" in user_agent:
        platform = "Windows"
    elif "Macintosh" in user_agent:
        platform = "macOS"
    else:
        platform = "Linux"
    headers.update(
        {
            "sec-ch-ua": ", ".join(f'"{brand}";v="{version}"' for brand, version in brands),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{platform}"',
        }
    )
    return headers


def chrome_request(request: httpx.Request) -> None:
    request.headers["Sec-Fetch-Site"] = "same-origin" if "Referer" in request.headers else "none"
    if request.method == "POST":
        request.headers["Origin"] = f"{request.url.scheme}://{request.url.netloc.decode()}"
        request.headers["Cache-Control"] = "max-age=0"


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
                browser = playwright.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    if user_agent is None:
                        blank = browser.new_page()
                        user_agent = str(blank.evaluate("navigator.userAgent"))
                        user_agent = user_agent.replace("HeadlessChrome/", "Chrome/")
                        blank.close()
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
                                if cookie.domain.lstrip(".") in {"accounts.lidl.com", "lidl.com"}
                            ]
                        )
                    page = context.new_page()

                    def navigation(route: Route) -> None:
                        request = route.request
                        if request.is_navigation_request() and request.frame == page.main_frame:
                            try:
                                _trusted(request.url)
                            except LoginError:
                                route.abort()
                                return
                        route.continue_()

                    page.route("**/*", navigation)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
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
                    except ValueError, AttributeError:
                        raise LoginError("captcha_configuration_unavailable") from None
                    if rules_response.status != 200 or not isinstance(key, str) or not key:
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
                    device_id = page.locator('form#login-form input[name="DeviceId"]').input_value()
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
