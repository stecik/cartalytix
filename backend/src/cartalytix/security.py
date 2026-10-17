import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash("unavailable-account-constant-timing-check")


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must contain 12 to 1024 characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        valid = _hasher.verify(password_hash or _dummy_hash, password)
    except VerificationError, InvalidHashError:
        return False
    return bool(password_hash) and valid


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
