"""Isolated Node-fetch login/refresh probe; Windows vault, no stored passwords."""

import argparse
import json
import logging
import math
import re
import shutil
import subprocess
import sys
from contextlib import suppress
from getpass import getpass
from gettext import gettext as _
from pathlib import Path
from typing import Any
from uuid import uuid4

from cartalytix.export import ExportError
from wolt.auth import Vault

TRANSPORT = Path(__file__).with_name("node_session.mjs")
SESSION_KEYS = ("access_token", "refresh_token", "device_token", "subject", "expires")
ERROR_CODES = frozenset(
    {
        "input_invalid",
        "token_invalid",
        "stored_session_invalid",
        "account_identity_mismatch",
        "network_error",
        "redirect_rejected",
        "rate_limited",
        "provider_unavailable",
        "provider_response_invalid",
        "challenge_required",
        "credentials_rejected",
        "auth_required",
        "refresh_rejected",
        "refresh_credentials_missing",
        "account_not_found",
        "password_login_unavailable",
        "login_identity_unavailable",
        "otp_rejected",
        "experiment_failed",
    }
)


class ExperimentError(ExportError):
    """Only fixed diagnostic codes cross the terminal boundary."""


class SessionVault(Vault):
    service = "Cartalytix.Foodora.NodeSession"
    chunk_size = 1024

    def _root(self) -> str | None:
        try:
            return self.backend.get_password(self.service, self.account)
        except Exception:  # noqa: BLE001 - Keyring errors can contain credential data.
            raise ExperimentError("credential_load_failed") from None

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        try:
            data = json.loads(value)
        except ValueError:
            raise ExperimentError("stored_session_invalid") from None
        if not isinstance(data, dict):
            raise ExperimentError("stored_session_invalid")
        return data

    def _parts(self, data: dict[str, Any]) -> list[str]:
        if "format" not in data:
            return []
        generation = data.get("generation")
        count = data.get("chunks")
        if (
            data.get("format") != "foodora-session-chunks-v1"
            or not isinstance(generation, str)
            or not re.fullmatch(r"[0-9a-f]{32}", generation)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= 64
        ):
            raise ExperimentError("stored_session_invalid")
        return [f"{self.service}.{generation}.{index}" for index in range(count)]

    def _remove_parts(self, names: list[str]) -> None:
        for name in names:
            # Cleanup errors must neither expose credentials nor undo the published root.
            with suppress(Exception):
                self.backend.delete_password(name, self.account)

    def load(self) -> dict[str, Any] | None:
        raw = self._root()
        if raw is None:
            return None
        data = self._decode(raw)
        names = self._parts(data)
        if not names:
            return data
        parts: list[str] = []
        for name in names:
            try:
                part = self.backend.get_password(name, self.account)
            except Exception:  # noqa: BLE001 - Keyring errors can contain credential data.
                raise ExperimentError("credential_load_failed") from None
            if not isinstance(part, str) or not part or len(part) > self.chunk_size:
                raise ExperimentError("stored_session_invalid")
            parts.append(part)
        return self._decode("".join(parts))

    def save(self, value: dict[str, Any]) -> None:
        kept = {key: value.get(key) for key in SESSION_KEYS}
        # JSON is ASCII; each part stays below Windows' UTF-16 credential limit.
        payload = json.dumps(kept, allow_nan=False, separators=(",", ":"))
        parts = [
            payload[index : index + self.chunk_size]
            for index in range(0, len(payload), self.chunk_size)
        ]
        if len(parts) > 64:
            raise ExperimentError("stored_session_too_large")
        previous = self._root()
        old_names = self._parts(self._decode(previous)) if previous is not None else []
        manifest = {
            "format": "foodora-session-chunks-v1",
            "generation": uuid4().hex,
            "chunks": len(parts),
        }
        names = self._parts(manifest)
        created: list[str] = []
        try:
            for name, part in zip(names, parts, strict=True):
                created.append(name)
                self.backend.set_password(name, self.account, part)
            # Publish only after all parts exist; failed writes preserve the old root.
            self.backend.set_password(self.service, self.account, json.dumps(manifest))
        except Exception:  # noqa: BLE001 - Keyring errors can contain credential data.
            self._remove_parts(created)
            raise ExperimentError("credential_store_failed") from None
        self._remove_parts(old_names)

    def clear(self) -> None:
        raw = self._root()
        if raw is None:
            return
        names = self._parts(self._decode(raw))
        try:
            # Keep the root until cleanup finishes, including across a failed retry.
            for name in names:
                if self.backend.get_password(name, self.account) is not None:
                    self.backend.delete_password(name, self.account)
            self.backend.delete_password(self.service, self.account)
        except Exception:  # noqa: BLE001 - Keyring errors can contain credential data.
            raise ExperimentError("credential_delete_failed") from None


def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise ExperimentError("node_runtime_missing")
    try:
        result = subprocess.run(
            [node, str(TRANSPORT)],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=100,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ExperimentError("transport_timeout") from None
    except OSError:
        raise ExperimentError("node_runtime_unavailable") from None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ExperimentError("transport_response_invalid") from None
    if not isinstance(data, dict):
        raise ExperimentError("transport_response_invalid")
    error = data.get("error")
    if error or result.returncode:
        raise ExperimentError(
            error
            if isinstance(error, str) and error in ERROR_CODES
            else "experiment_failed"
        )
    return data


def sign_in(username: str, password: str) -> dict[str, Any]:
    result = invoke({"command": "login", "username": username, "password": password})
    if result.get("status") != "otp_required":
        return result
    methods = result.get("methods")
    if not isinstance(methods, list):
        raise ExperimentError("otp_method_unavailable")
    available = [method for method in methods if method in {"EMAIL", "SMS", "CALL"}]
    if not available:
        raise ExperimentError("otp_method_unavailable")
    print(
        _("Foodora requires verification. Available methods: {methods}").format(
            methods=", ".join(available)
        )
    )
    method = input(_("Method (sending a code or call): ")).strip().upper()
    if method not in available:
        raise ExperimentError("otp_method_invalid")
    payload = {
        "command": "otp",
        "username": username,
        "password": password,
        "method": method,
        "cookies": result.get("cookies", {}),
    }
    sent = invoke(payload)
    if sent.get("status") != "otp_required":
        return sent
    payload["cookies"] = sent.get("cookies", {})
    payload["otp"] = getpass(_("Verification code (not stored): ")).strip()
    try:
        return invoke(payload)
    finally:
        payload.clear()


def save_result(vault: SessionVault, result: dict[str, Any]) -> dict[str, object]:
    session = result.get("session")
    report = result.get("report")
    if (
        not isinstance(session, dict)
        or not isinstance(report, dict)
        or report.get("authenticated") is not True
    ):
        raise ExperimentError("transport_response_invalid")
    if not all(
        isinstance(session.get(key), str) and session.get(key)
        for key in SESSION_KEYS[:-1]
    ):
        raise ExperimentError("transport_response_invalid")
    # Explicit whitelist: the Node bridge carries secrets; terminal reports never do.
    kept = {
        key: report.get(key)
        for key in (
            "authenticated",
            "refresh_available",
            "refresh_verified",
            "refresh_attempted",
            "refresh_http_status",
            "long_term_refresh_verified",
            "expires",
            "access_rotated",
            "refresh_rotated",
            "device_rotated",
        )
        if key in report
    }
    for key, value in kept.items():
        if key == "expires":
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ExperimentError("transport_response_invalid")
        elif key == "refresh_http_status":
            if value != 200:
                raise ExperimentError("transport_response_invalid")
        elif not isinstance(value, bool):
            raise ExperimentError("transport_response_invalid")
    vault.save(session)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(
        description=_("Experimental Foodora login and session refresh through Node.js")
    )
    parser.add_argument("command", choices=["login", "status", "refresh", "logout"])
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    stage = "credential_store_init"
    try:
        vault = SessionVault()
        if args.command == "logout":
            stage = "credential_store_delete"
            vault.clear()
            print(_("Local experimental session deleted; Foodora session not revoked."))
            return
        if args.command == "login":
            if not sys.stdin.isatty():
                raise ExperimentError("interactive_terminal_required")
            username = input(_("Foodora email: ")).strip()
            password = getpass(_("Foodora password (not stored): "))
            print(_("Signing in without opening a browser window..."), flush=True)
            try:
                stage = "login"
                result = sign_in(username, password)
            finally:
                password = ""
        else:
            stage = "credential_store_load"
            session = vault.load()
            if not isinstance(session, dict):
                raise ExperimentError("stored_session_invalid_or_missing")
            stage = args.command
            result = invoke({"command": args.command, "session": session})
        stage = "credential_store_save"
        print(json.dumps(save_result(vault, result), indent=2, allow_nan=False))
    except (ExperimentError, ExportError) as error:
        print(
            _("Foodora experiment failed: {code}").format(code=str(error)),
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        print(_("Cancelled."), file=sys.stderr)
        sys.exit(130)
    except Exception:  # noqa: BLE001 - Third-party exceptions may contain credentials.
        print(
            _(
                "Experiment failed at {stage}; no credentials or provider response were logged."
            ).format(stage=stage),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
