import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash("unavailable-account-constant-timing-check")


PASSWORD_RULE = (
    "Password must contain 8 to 1024 characters, including an uppercase letter, "
    "a lowercase letter and a special character"
)


def is_strong_password(password: str) -> bool:
    return (
        8 <= len(password) <= 1024
        and any(character.isupper() for character in password)
        and any(character.islower() for character in password)
        and any(not character.isalnum() for character in password)
    )


def hash_password(password: str) -> str:
    if not is_strong_password(password):
        raise ValueError(PASSWORD_RULE)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        valid = _hasher.verify(password_hash or _dummy_hash, password)
    except VerificationError, InvalidHashError:
        return False
    return bool(password_hash) and valid


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
