from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from cartalytix.settings import Settings


class CredentialError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self, settings: Settings) -> None:
        if not settings.token_key_file:
            raise CredentialError("credential_key_unavailable")
        try:
            self._fernet = Fernet(Path(settings.token_key_file).read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise CredentialError("credential_key_unavailable") from exc

    def encrypt(self, token: str) -> bytes:
        if not token:
            raise CredentialError("invalid_refresh_token")
        return self._fernet.encrypt(token.encode("utf-8"))

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise CredentialError("credential_decryption_failed") from exc
