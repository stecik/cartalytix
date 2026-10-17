from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.settings import Settings


def test_credentials_require_an_external_key_and_reject_tampering(tmp_path: Path) -> None:
    with pytest.raises(CredentialError):
        TokenCipher(Settings(token_key_file=None))
    key = tmp_path / "token.key"
    key.write_bytes(Fernet.generate_key())
    cipher = TokenCipher(Settings(token_key_file=str(key)))
    encrypted = cipher.encrypt("synthetic-refresh-token")
    assert b"synthetic-refresh-token" not in encrypted
    assert cipher.decrypt(encrypted) == "synthetic-refresh-token"
    with pytest.raises(CredentialError):
        cipher.decrypt(encrypted[:-4] + b"0000")
    key.write_bytes(Fernet.generate_key())
    with pytest.raises(CredentialError):
        TokenCipher(Settings(token_key_file=str(key))).decrypt(encrypted)
