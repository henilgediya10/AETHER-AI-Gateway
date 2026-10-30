import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt  # this is the PyJWT package: install it with `pip install pyjwt`
from dotenv import load_dotenv
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

load_dotenv()

ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))


def _load_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY is missing or shorter than 32 characters. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "and put it in backend/.env as JWT_SECRET_KEY=..."
        )
    return secret


# Checked once at startup, so a bad configuration fails immediately
# instead of causing a 500 error on the first login.
SECRET_KEY = _load_secret()


# ---------------- JWT tokens ----------------

def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # PyJWT requires "sub" to be a string
        "iat": now,
        "jti": secrets.token_urlsafe(24),  # unique per login/session
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError if the token is invalid, expired or malformed."""
    return jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM],  # pinned: tokens signed with any other algorithm are rejected
        options={"require": ["exp", "sub"]},
    )


# ---------------- Passwords (Argon2) ----------------

_hasher = PasswordHash.recommended()  # Argon2id

# Used when the email does not exist, so login takes the same time either way.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if hashed_password is None:
        _hasher.verify(plain_password, _DUMMY_HASH)  # burn the same time
        return False
    try:
        return _hasher.verify(plain_password, hashed_password)
    except UnknownHashError:  # corrupted or non-Argon2 value in the database
        return False


def verify_and_rehash(
    plain_password: str, hashed_password: str | None
) -> tuple[bool, str | None]:
    """Returns (is_valid, new_hash_or_None). Save new_hash if it is not None."""
    if hashed_password is None:
        _hasher.verify(plain_password, _DUMMY_HASH)
        return False, None
    try:
        return _hasher.verify_and_update(plain_password, hashed_password)
    except UnknownHashError:
        return False, None