"""Security primitives: password hashing (scrypt) + JWT tokens + TOTP MFA + recovery codes.

Everything here is stdlib cryptography (hashlib/hmac/secrets) plus PyJWT — no external auth
service and no dependency the offline demo would need to download.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from backend.config import settings

# ---------- password policy ----------

COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "12345678", "123456789", "qwerty123",
        "letmein", "admin123", "welcome1", "iloveyou", "changeme", "passw0rd",
    }
)


def validate_password_strength(password: str) -> list[str]:
    """Return a list of policy violations (empty list = acceptable).

    Deliberately length-first (the single strongest factor) plus a small denylist; composition
    rules that push users toward predictable substitutions are intentionally avoided.
    """
    problems: list[str] = []
    if len(password or "") < settings.PASSWORD_MIN_LENGTH:
        problems.append(f"must be at least {settings.PASSWORD_MIN_LENGTH} characters")
    if password and password.lower() in COMMON_PASSWORDS:
        problems.append("is a commonly used password")
    if password and len(set(password)) == 1:
        problems.append("must not be a single repeated character")
    return problems


def hash_password(password: str) -> str:
    """Hash a password with scrypt (N=2^14) + per-hash salt. Format: scrypt$n$r$p$salt$hash."""
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a scrypt-formatted hash."""
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(bytes.fromhex(hash_hex)),
        )
        return hmac.compare_digest(dk, bytes.fromhex(hash_hex))
    except Exception:
        return False


def create_access_token(
    subject: str,
    role: str,
    extra: dict | None = None,
    *,
    sid: str = "",
    token_type: str = "access",
    minutes: int | None = None,
) -> str:
    """Issue a signed JWT.

    ``sid`` ties the token to a server-side session row so it can be REVOKED (logout, password
    change, "sign out other devices") — a stateless token alone cannot be invalidated. ``typ``
    distinguishes an access token from a short-lived MFA challenge / refresh token so one can
    never be replayed as another.
    """
    now = datetime.now(timezone.utc)
    exp_minutes = minutes if minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": subject,
        "role": role,
        "typ": token_type,
        "sid": sid,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def create_refresh_token(subject: str, sid: str) -> str:
    return create_access_token(
        subject, "", {}, sid=sid, token_type="refresh",
        minutes=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60,
    )


def create_mfa_challenge_token(subject: str) -> str:
    return create_access_token(
        subject, "", {}, token_type="mfa", minutes=settings.MFA_CHALLENGE_EXPIRE_MINUTES,
    )


def decode_token(token: str, *, expected_type: str | None = None) -> dict | None:
    """Decode and validate a JWT. Returns None on any failure (never raises).

    When ``expected_type`` is given, a token of a different type is rejected — an MFA challenge
    token can never be presented as an access token, and vice versa.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if expected_type is not None and payload.get("typ", "access") != expected_type:
        return None
    return payload


def new_session_id() -> str:
    return uuid.uuid4().hex


# ---------- TOTP (RFC 6238, SHA-1/30s — the authenticator-app standard) ----------

def generate_totp_secret() -> str:
    """A base32 shared secret, as accepted by Google Authenticator / Authy / 1Password."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_at(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    """Current TOTP code — used to build the otpauth provisioning URI / tests."""
    return _totp_at(secret, int((at if at is not None else time.time()) // 30))


def verify_totp(secret: str, code: str, *, window: int = 1, at: float | None = None) -> bool:
    """Verify a 6-digit code, tolerating one 30s step of clock drift either way."""
    if not secret or not code:
        return False
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 6:
        return False
    step = int((at if at is not None else time.time()) // 30)
    return any(
        hmac.compare_digest(_totp_at(secret, step + offset), cleaned)
        for offset in range(-window, window + 1)
    )


def totp_provisioning_uri(secret: str, account: str) -> str:
    from urllib.parse import quote

    issuer = settings.MFA_ISSUER
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )


# ---------- recovery codes ----------

def generate_recovery_codes(count: int | None = None) -> list[str]:
    """Human-copyable one-time recovery codes (shown exactly once, stored only as hashes)."""
    n = count or settings.MFA_RECOVERY_CODE_COUNT
    codes = []
    for _ in range(n):
        raw = secrets.token_hex(5).upper()  # 10 hex chars
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code with a salted scrypt digest (never store the plaintext)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(code.strip().upper().encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_recovery_code(code: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            code.strip().upper().encode("utf-8"),
            salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32,
        )
        return hmac.compare_digest(dk, bytes.fromhex(hash_hex))
    except Exception:
        return False


def token_fingerprint(token: str) -> str:
    """A one-way fingerprint (for storing reset/verification tokens without the plaintext)."""
    return hashlib.sha256(f"{settings.SECRET_KEY}:{token}".encode("utf-8")).hexdigest()
