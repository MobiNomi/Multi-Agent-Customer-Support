"""Password hashing with Argon2 (the current recommendation for storing passwords).

A hash can be checked against a password but never turned back into it, and each hash has its own
random salt, so two customers with the same password still get different hashes.
"""
from pwdlib import PasswordHash

MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHash.recommended()
# Checked when the email doesn't exist, so a wrong email takes as long as a wrong password
# and the response time doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:  # malformed hash in the database
        return False


def burn_verify_time(password: str) -> None:
    _hasher.verify(password, _DUMMY_HASH)
