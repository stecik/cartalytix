// Private stdin/stdout bridge for session.py; stdout may contain session credentials.
import { pathToFileURL } from "node:url";

const SITE = "https://www.foodora.cz";
const LOGIN = `${SITE}/login/new`;
const HISTORY = "https://cz.fd-api.com/api/v5/orders/order_history?include=order_products,order_details&offset=0&limit=1&language_id=3";
const COOKIE_NAMES = ["token", "refresh_token", "device_token"];
const METHODS = ["EMAIL", "SMS", "CALL"];

/** @typedef {{access_token:string,refresh_token:string,device_token:string,subject:string,expires:number}} Session */
/** @typedef {Record<string, unknown>} Input */

export class ExperimentError extends Error {
  /** @param {string} code */
  constructor(code) { super(code); this.code = code; }
}

/** @param {unknown} value @returns {value is Record<string, any>} */
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }

/** @param {unknown} value @returns {value is string} */
function credential(value) {
  return typeof value === "string" && /^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,16384}$/.test(value);
}

/** @param {string} access @returns {{subject:string,expires:number}} */
function claims(access) {
  const parts = access.split(".");
  if (parts.length !== 3 || !/^[A-Za-z0-9_-]+$/.test(parts[1])) throw new ExperimentError("token_invalid");
  let data;
  try { data = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8")); }
  catch { throw new ExperimentError("token_invalid"); }
  if (!object(data) || !["string", "number"].includes(typeof data.user_id)
    || !String(data.user_id).trim() || String(data.user_id).length > 256
    || (typeof data.user_id === "number" && !Number.isSafeInteger(data.user_id))
    || typeof data.expires !== "number" || !Number.isFinite(data.expires) || data.expires <= 0) {
    throw new ExperimentError("token_invalid");
  }
  return {subject: String(data.user_id), expires: data.expires > 1e12 ? data.expires / 1000 : data.expires};
}

/** @param {unknown} value @returns {Session} */
function readSession(value) {
  if (!object(value) || !credential(value.access_token) || !credential(value.refresh_token)
    || !credential(value.device_token) || typeof value.subject !== "string") {
    throw new ExperimentError("stored_session_invalid");
  }
  const parsed = claims(value.access_token);
  if (parsed.subject !== value.subject) throw new ExperimentError("account_identity_mismatch");
  return {access_token: value.access_token, refresh_token: value.refresh_token,
    device_token: value.device_token, ...parsed};
}

/** @param {Input} input @param {typeof fetch} fetcher */
export async function execute(input, fetcher = fetch) {
  if (!object(input)) throw new ExperimentError("input_invalid");
  const jar = new Map();
  const old = ["refresh", "status"].includes(String(input.command)) ? readSession(input.session) : null;
  if (old) {
    jar.set("token", old.access_token); jar.set("refresh_token", old.refresh_token); jar.set("device_token", old.device_token);
  } else if (object(input.cookies)) {
    for (const name of COOKIE_NAMES) {
      const value = input.cookies[name];
      if (credential(value)) jar.set(name, value);
    }
  }

  /** @param {string} url @param {string} stage @param {Record<string,unknown>|undefined} body @param {Record<string,string>} extra @param {boolean} json */
  async function request(url, stage, body = undefined, extra = {}, json = true) {
    const headers = {Accept: "application/json", ...extra};
    if (url.startsWith(`${SITE}/`)) {
      headers.Origin = SITE; headers.Referer = `${SITE}/`;
      if (jar.size) headers.Cookie = [...jar].map(([name,value]) => `${name}=${encodeURIComponent(value)}`).join("; ");
    }
    if (body) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetcher(url, {method: body ? "POST" : "GET", headers,
        ...(body ? {body: JSON.stringify(body)} : {}), redirect: "manual", signal: AbortSignal.timeout(20000)});
    } catch { throw new ExperimentError("network_error"); }
    if (response.status >= 300 && response.status < 400) throw new ExperimentError("redirect_rejected");
    if (response.status === 429) throw new ExperimentError("rate_limited");
    if (response.status >= 500) throw new ExperimentError("provider_unavailable");
    let data = {};
    if (json) {
      try { data = await response.json(); }
      catch { throw new ExperimentError("provider_response_invalid"); }
      if (!object(data)) throw new ExperimentError("provider_response_invalid");
      if (data.appId || data.blockScript) throw new ExperimentError("challenge_required");
    }
    if (url.startsWith(`${SITE}/`)) {
      for (const raw of response.headers.getSetCookie()) {
        const [pair, ...attributes] = raw.split(";");
        const split = pair.indexOf("=");
        const name = pair.slice(0, split).trim();
        if (!COOKIE_NAMES.includes(name)) continue;
        const attrs = new Map(attributes.map(part => {
          const p = part.trim(); const i = p.indexOf("=");
          return i < 0 ? [p.toLowerCase(), ""] : [p.slice(0,i).toLowerCase(), p.slice(i+1)];
        }));
        const domain = (attrs.get("domain") || "www.foodora.cz").replace(/^\./, "").toLowerCase();
        if (!["foodora.cz", "www.foodora.cz"].includes(domain) || attrs.get("path") !== "/") continue;
        if ((attrs.has("max-age") && Number(attrs.get("max-age")) <= 0)
          || (attrs.has("expires") && Date.parse(attrs.get("expires")) <= Date.now())) {
          jar.delete(name); continue;
        }
        let value;
        try { value = decodeURIComponent(pair.slice(split+1)); }
        catch { throw new ExperimentError("provider_response_invalid"); }
        if (!credential(value)) throw new ExperimentError("provider_response_invalid");
        jar.set(name, value);
      }
    }
    const exception = object(data.data) ? data.data.exception_type : null;
    if (stage === "login" && ["invalid_otp", "invalid_otp_method"].includes(exception)) {
      if (input.otp) throw new ExperimentError("otp_rejected");
      const methods = (response.headers.get("x-otp-method") || "").split(",").map(x => x.trim()).filter(x => METHODS.includes(x));
      const fallback = typeof input.method === "string" && METHODS.includes(input.method) ? [input.method] : [];
      return {otp: true, methods: methods.length ? methods : fallback};
    }
    if (response.status === 401 || response.status === 403) {
      throw new ExperimentError(stage === "login" ? "credentials_rejected" : "auth_required");
    }
    if (response.status !== 200 || data.error || data.success === false || exception
      || (typeof data.status_code === "number" && data.status_code >= 400)) {
      throw new ExperimentError(stage === "login" ? "credentials_rejected" : stage === "refresh" ? "refresh_rejected" : "provider_response_invalid");
    }
    return data;
  }

  /** @param {string|undefined} expected @returns {Promise<Session>} */
  async function verify(expected = undefined) {
    const access = jar.get("token"), refresh = jar.get("refresh_token"), device = jar.get("device_token");
    if (![access, refresh, device].every(credential)) throw new ExperimentError("refresh_credentials_missing");
    const parsed = claims(access);
    if (expected !== undefined && parsed.subject !== expected) throw new ExperimentError("account_identity_mismatch");
    if (parsed.expires <= Date.now()/1000) throw new ExperimentError("auth_required");
    const data = await request(HISTORY, "identity", undefined, {Authorization: `Bearer ${access}`, "X-FP-API-KEY": "volo"});
    if (!object(data.data) || !Array.isArray(data.data.items)) throw new ExperimentError("provider_response_invalid");
    return {access_token: access, refresh_token: refresh, device_token: device, ...parsed};
  }

  /** @param {Session} session @param {boolean} attempted */
  function outcome(session, attempted) {
    return {session, report: {authenticated: true, refresh_available: true, expires: session.expires,
      refresh_attempted: attempted,
      refresh_verified: Boolean(old && attempted && session.access_token !== old.access_token),
      long_term_refresh_verified: false,
      ...(old && attempted ? {refresh_http_status: 200, access_rotated: session.access_token !== old.access_token,
        refresh_rotated: session.refresh_token !== old.refresh_token, device_rotated: session.device_token !== old.device_token} : {})}};
  }

  if (old) {
    if (input.command === "status") {
      try { return outcome(await verify(old.subject), false); }
      catch (error) { if (!(error instanceof ExperimentError) || error.code !== "auth_required") throw error; }
    }
    await request(`${LOGIN}/api/refresh-token`, "refresh", {country: "cz", platform: "b2c",
      device_token: old.device_token, refresh_token: old.refresh_token}, {"X-FP-API-KEY": "volo"});
    return outcome(await verify(old.subject), true);
  }
  if (!["login", "otp"].includes(String(input.command)) || typeof input.username !== "string"
    || !input.username.trim() || input.username.length > 254 || typeof input.password !== "string"
    || !input.password || input.password.length > 1024) throw new ExperimentError("input_invalid");
  const username = input.username.trim();
  if (input.command === "login") {
    await request(LOGIN, "bootstrap", undefined, {}, false);
    const checked = await request(`${LOGIN}/api/email-check`, "email", {email: username});
    if (checked.is_known !== true) throw new ExperimentError("account_not_found");
    if (checked.has_password !== true) throw new ExperimentError("password_login_unavailable");
  }
  const extra = {};
  if (jar.has("device_token")) extra["X-Device"] = jar.get("device_token");
  if (input.command === "otp") {
    if (typeof input.method !== "string" || !METHODS.includes(input.method)) throw new ExperimentError("input_invalid");
    extra["X-OTP-Method"] = input.method;
    if (input.otp !== undefined) {
      if (typeof input.otp !== "string" || !/^[0-9]{4,10}$/.test(input.otp)) throw new ExperimentError("input_invalid");
      extra["X-OTP"] = input.otp;
    }
  }
  // A successful login must issue its own access token, including after OTP.
  jar.delete("token");
  const data = await request(`${LOGIN}/api/login`, "login", {username, password: input.password, platform: "b2c", incogniaToken: ""}, extra);
  if (data.otp) return {status: "otp_required", methods: data.methods, cookies: Object.fromEntries(jar)};
  if (!["string", "number"].includes(typeof data.user_id) || !String(data.user_id).trim()
    || String(data.user_id).length > 256 || (typeof data.user_id === "number" && !Number.isSafeInteger(data.user_id))) {
    throw new ExperimentError("login_identity_unavailable");
  }
  // Web login metadata is not a documented identity claim. Bind the session to
  // the freshly issued JWT only after the authenticated API accepts that token.
  return outcome(await verify(), false);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let text = "";
    process.stdin.setEncoding("utf8");
    for await (const chunk of process.stdin) {
      text += chunk;
      if (text.length > 100000) throw new ExperimentError("input_invalid");
    }
    const result = await execute(JSON.parse(text));
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    process.stdout.write(JSON.stringify({error: error instanceof ExperimentError ? error.code : "experiment_failed"}));
    process.exitCode = 1;
  }
}
