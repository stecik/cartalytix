# Lidl login experiment: HTTP and browser-assisted variants

Status: local experiment, not the production connection UI. A real CZ attempt
stopped at device verification; pure HTTP login has not succeeded. Do not
interpret mocked tests as provider support.
The hybrid variant now passes anonymous Linux initialization; a real hybrid login
is pending user testing.

## Purpose

Test whether a server can perform Lidl Plus authorization using HTTP requests,
initially without Chrome or Playwright, then with headless device initialization.
Neither variant requires an interactive desktop. This is distinct
from capturing another tab's traffic, which browser origin isolation prevents.

The local form collects Lidl email/password, then an SMS/email code if requested.
It uses the existing authenticated Cartalytix session and a selected, owned Lidl
connection. Successful authorization passes the refresh token directly to the
existing Cartalytix authorization endpoint, which validates and encrypts it.
The experiment does not trigger a receipt synchronization automatically.

## Protocol

1. Authorize the native Lidl Plus client with PKCE S256, random state and nonce,
   Czech country/language and its registered custom-scheme callback.
2. Load the login page and retain its cookies, CSRF token and form model.
3. Submit email/password once; continue with the verification code when needed.
4. Validate the callback state, exchange its code once and hand off the refresh
   token. Never follow the custom scheme or an external redirect with credentials.

All provider traffic is HTTPS to `accounts.lidl.com`. The client does not retry
credential submission, verification or token exchange. An uncertain POST ends
the attempt; reload the local page to create a new flow.

## Local test deployment

`work/compose.http-probe.yaml` is an ignored, temporary Compose override alongside
the existing local test override. Its separate image extends the existing API
image with Playwright 1.64.0 and full Chromium (`channel="chromium"`, new headless
mode). This folder is mounted read-only, with one Uvicorn worker and port
`127.0.0.1:18081:18081`. No database password or encryption key is passed to this
service. The container has a temporary in-memory `/tmp`, a read-only root, an
init process, and memory/process limits.
The internal API base is selected by `CARTALYTIX_HTTP_PROBE_BACKEND`; the public
application origin remains `http://localhost:18080`.
`CARTALYTIX_HTTP_PROBE_HYBRID=1` selects the hybrid variant. It starts a fresh,
anonymous browser context with no account password or persistent profile, waits
for the device cookie, obtains a fresh reCAPTCHA Enterprise token using the
official page's `LOGIN` action, copies the Lidl-domain cookies (including scoped
paths) and its user agent, then closes Chromium. Third-party cookies are excluded.
The HTTP client reloads the same login
document, keeping the PKCE flow and cookie scope/expiry/flags. Lidl's `#login`
client route is removed before HTTP navigation. Credentials are submitted after
that reload, including the mark in `DeviceId`. Only one browser initialization
runs at a time. Before an MFA form POST, another short browser initialization
uses the existing cookies and user agent to obtain a new, once-only CAPTCHA
token. Passwords and verification codes are never supplied to the browser.
No browser/CDP port is exposed. Interactive CAPTCHA challenges stop the probe.

Sign in to Cartalytix at `http://localhost:18080`, then open
`http://localhost:18081`. Use `localhost` for both: cookies are host-specific.
Enter secrets only in this local form. Do not send them through chat or copy
provider responses into logs. Keep this probe bound to loopback; public-server
integration would require a separate deployment review and HTTPS UI.

The probe checks application ownership, a per-flow CSRF token and the browser
Origin. Flows expire after ten minutes; a background sweep runs every thirty
seconds, with additional cleanup on requests and shutdown. Busy flows close
when their current operation finishes and the next sweep can acquire their lock.
There are at most sixteen flows. Provider state stays in memory, and the client
is closed after failure or token handoff. Logs contain fixed error codes and only
HTTP request count, status, browser initialization flag and device-cookie presence. Access
logging is disabled. Validation errors never echo submitted credentials.

## Verified on 2026-10-17

- Anonymous Czech authorization reaches the password form over HTTP: two requests,
  status 200, no `lidl-sso-browser-mark` cookie, default OTP method null.
- The original HTTP probe ran in Linux Docker without Playwright. The hybrid
  variant uses a separate image with Playwright/Chromium.
- Browser smoke test: existing Cartalytix login, owned connection selector, blank
  password, hidden OTP step, no mobile overflow or JavaScript errors. No Lidl
  credentials submitted by the automated test.
- Thirty-three mocked tests pass: password/MFA/code exchange, callback state,
  foreign redirects, error parsing, uncertain POST handling, authenticated form,
  owner/CSRF/Origin protection, generic validation, once-only token handoff,
  browser-to-HTTP identity/cookie transfer, rejection of invalid browser state,
  and expiry of abandoned state without subsequent requests.
- A user hybrid attempt with the device mark present returned the former generic
  `credentials_rejected` result. Its exact provider error was not retained, so it
  does not establish an incorrect password. Public login modules show that
  password and OTP form submissions also include `ReCaptchaToken`, which this
  attempt omitted. This protocol omission is corrected; whether it explains the
  observed rejection still requires a user-submitted retry.
- Anonymous Linux initialization obtains a nonempty CAPTCHA token from the
  official script; a second initialization produces a fresh token while retaining
  user agent, device mark and antiforgery cookie. No credentials were submitted
  and no token values were logged. Mocked tests ensure password and OTP submissions
  use distinct CAPTCHA tokens and scoped session cookies survive export.
- Error classification recognizes only known field names/codes: a
  `ReCaptchaToken` field error is `captcha_required`; unknown provider errors are
  `login_rejected`, and an unchanged form is `login_flow_incomplete`. Diagnostics
  include only booleans, never raw provider error messages or arbitrary keys.
- A subsequent user attempt stopped at `captcha_token_unavailable` with zero
  HTTP-client requests, before password submission. Its original exception was
  not retained, so the cause remains unconfirmed. Anonymous initialization through
  the real authenticated server UI subsequently passed three consecutive checks.
- `Check initialization (no password)` calls `/check` with only the flow CSRF
  token. Auth, owner, Origin and CSRF are checked; extra fields are forbidden.
  It creates and always closes a temporary anonymous client, calls only `begin()`,
  and neither consumes the login attempt nor sends credentials or obtains an
  account token. An active login flow cannot run this check.
- CAPTCHA script, invalid-client, network, execution-timeout and other execution
  failures now have distinct fixed codes. Synchronous `execute()` errors inside
  the official `ready()` callback reject the promise immediately instead of
  leaving it pending. No raw exception messages enter the UI or logs.
- The user's next initialization check passed. A subsequent real login returned
  a `Password` field error, with `captcha_attached=true`,
  `captcha_field_error=false` and `password_field_error=true`. This establishes
  the rejected field; it does not reveal the provider's reason or independently
  prove that every CAPTCHA assessment passed. The current public password
  component posts the input unchanged as `Password`, alongside `Step=1`, matching
  this client's format. Fresh login with the same credentials directly at Lidl
  versus a federated Google/Apple login remains to be checked by the user.
- Ruff format/lint and ty pass for this experiment.
- User-submitted CZ attempt from the Linux HTTP probe stopped at
  `device_verification_required`: three requests, final HTTP status 200, no
  `lidl-sso-browser-mark` cookie. No authorization code/token was obtained.
  This result does not establish whether the password was correct.
- Follow-up anonymous Windows Chrome **headless** test reached the official
  login page and obtained a nonempty `lidl-sso-browser-mark` cookie, with zero
  JavaScript errors. No credentials were filled, and no cookie value was logged
  or persisted. This is evidence for browser-based device initialization, not
  successful authentication.
- Anonymous hybrid Linux test passed: Chromium obtained the mark and closed;
  HTTP then reached the password form with `browser_bootstrap=true`, HTTP status
  200 and `has_device_mark=true`. No credentials were submitted. This proves
  cookie handoff to HTTP, but does not prove Lidl accepts it for credentials.

Pending: successful CZ password/MFA flow in the hybrid variant, actual token
handoff and receipt sync using the resulting token. Existing receipt
synchronization tests are separate evidence. The hybrid introduces a browser
dependency and must not be described as a browserless solution.

## Expected limitation

Lidl's public Czech client rules currently advertise device verification (UBI).
An HTTP client can follow its own requests, but does not execute JavaScript that
generates device attestations. The real CZ attempt was rejected at that check;
the HTTP-only Go reference documents the same requirement for enforcing countries.
Device/CAPTCHA errors stop the experiment. TLS fingerprint libraries such as curl_cffi do not
automatically provide JavaScript execution or solve device verification.

This is an unofficial, unregistered integration using the native client protocol.
Lidl can change form fields, client permissions, MFA, device checks and token
behavior. A working flow in another country or repository is not proof for CZ.

References: [HTTP-only Go implementation](https://github.com/klajbard/lidl-plus-go#authentication),
[its form parser](https://github.com/klajbard/lidl-plus-go/blob/main/lidlplus/page.go),
[Home Assistant implementation](https://github.com/FaserF/ha-lidl/blob/main/custom_components/lidl/config_flow.py),
[curl_cffi documentation](https://curl-cffi.readthedocs.io/en/latest/).
