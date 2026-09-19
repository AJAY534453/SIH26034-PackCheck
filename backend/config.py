"""POCKET configuration — environment-driven with safe defaults (offline, ₹0)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv ships with rapidocr; optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


class Settings:
    """Central configuration. All values overridable via POCKET_* / AI_* env vars."""

    APP_NAME: str = _env("PACKCHECK_APP_NAME", "PACKCHECK AI")
    APP_SHORT_NAME: str = "PACKCHECK"
    APP_SUBTITLE: str = "AI-Assisted Legal Metrology Inspection System"
    APP_TAGLINE: str = "Scan → Extract → Verify → Alert → Report"
    APP_TEAM: str = "T_IDEA SUPER KINGS"
    APP_PROBLEM: str = "SIH26034"
    APP_VERSION: str = "2.0.0"

    HOST: str = _env("POCKET_HOST", "127.0.0.1")
    PORT: int = int(_env("POCKET_PORT", "8001"))
    FRONTEND_ORIGIN: str = _env("POCKET_FRONTEND_ORIGIN", "http://localhost:5174")

    DATABASE_URL: str = _env("POCKET_DATABASE_URL", f"sqlite:///{(BASE_DIR / 'storage' / 'pocket.db').as_posix()}")

    SECRET_KEY: str = _env("POCKET_SECRET_KEY", "pocket-dev-secret-change-me-in-production-0123456789")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(_env("POCKET_ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

    # ---------- session security ----------
    # Refresh tokens are long-lived but tracked server-side (rotated and revocable per session).
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(_env("POCKET_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    # Session cookies are HttpOnly + SameSite by default. `Secure` must be set once the app is
    # served over HTTPS — enabling it on plain-HTTP localhost would stop the cookie being sent.
    SECURE_COOKIES: bool = _env("POCKET_SECURE_COOKIES", "0") == "1"
    COOKIE_SAMESITE: str = _env("POCKET_COOKIE_SAMESITE", "lax")  # lax | strict | none
    COOKIE_DOMAIN: str = _env("POCKET_COOKIE_DOMAIN", "")

    # ---------- brute-force protection ----------
    LOGIN_MAX_FAILED: int = int(_env("POCKET_LOGIN_MAX_FAILED", "5"))
    LOGIN_LOCKOUT_MINUTES: int = int(_env("POCKET_LOGIN_LOCKOUT_MINUTES", "15"))
    LOGIN_WINDOW_SECONDS: int = int(_env("POCKET_LOGIN_WINDOW_SECONDS", "300"))
    LOGIN_IP_MAX_ATTEMPTS: int = int(_env("POCKET_LOGIN_IP_MAX_ATTEMPTS", "20"))

    # ---------- password policy ----------
    PASSWORD_MIN_LENGTH: int = int(_env("POCKET_PASSWORD_MIN_LENGTH", "8"))
    RESET_TOKEN_EXPIRE_MINUTES: int = int(_env("POCKET_RESET_TOKEN_EXPIRE_MINUTES", "30"))

    # ---------- MFA ----------
    MFA_ISSUER: str = _env("POCKET_MFA_ISSUER", "PACKCHECK AI")
    MFA_CHALLENGE_EXPIRE_MINUTES: int = int(_env("POCKET_MFA_CHALLENGE_EXPIRE_MINUTES", "5"))
    MFA_RECOVERY_CODE_COUNT: int = int(_env("POCKET_MFA_RECOVERY_CODE_COUNT", "8"))

    STORAGE_DIR: Path = Path(_env("POCKET_STORAGE_DIR", str(BASE_DIR / "storage")))

    MAX_UPLOAD_MB: int = int(_env("POCKET_MAX_UPLOAD_MB", "15"))
    ALLOWED_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")
    ALLOWED_MIME: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")

    DEMO_MODE: bool = _env("POCKET_DEMO_MODE", "1") == "1"

    # ---------- vision extraction (optional, server-side only) ----------
    #
    # The application runs the complete on-device OCR pipeline with no provider at all. A
    # vision provider is an ADDITIONAL perception source whose readings become ordinary
    # candidates, validated and ranked by the deterministic pipeline like any other evidence.
    # The API key lives only in this process's environment; it is never sent to the browser.
    AI_PROVIDER: str = _env("AI_PROVIDER", "gemini")
    AI_MODEL: str = _env("AI_MODEL", "gemini-2.5-flash")
    GEMINI_API_KEY: str = _env("GEMINI_API_KEY", _env("GOOGLE_API_KEY", ""))
    AI_TIMEOUT_SECONDS: int = int(_env("AI_TIMEOUT_SECONDS", "60"))
    # auto = use the provider when a key is present; 0 = never call out to a provider.
    AI_ENABLED: str = _env("AI_ENABLED", "auto")
    # A price gap below this fraction of MRP is not worth an inspector's attention.
    BILL_PRICE_TOLERANCE_PCT: float = float(_env("BILL_PRICE_TOLERANCE_PCT", "0.5"))

    # Weighted compliance score (0-100) at or above which the automated review may issue a
    # pass-oriented PRELIMINARY verdict; below it the scan is flagged for official finalization.
    AI_COMPLIANCE_THRESHOLD: float = float(_env("POCKET_AI_COMPLIANCE_THRESHOLD", "85"))

    # Minimum share of the applicable rule set that must actually be VERIFIED from evidence for
    # the automated review to issue a pass-oriented verdict. Compliance % and coverage % are two
    # different measurements: without this floor, an unreadable label could pass on a high
    # compliance score computed from the handful of requirements that happened to be readable.
    #
    # 70 is a calibrated default, not an arbitrary one: measured over the seeded corpus, a floor of
    # 80 made a preliminary pass unreachable for every scan (the best evidence coverage observed
    # was 76.9%, because an uncalibrated font-size check and an unseen import image are genuinely
    # unverifiable), which would defeat the point of the threshold rule. Raise it for a stricter
    # regime — the floor only ever makes the automated review more conservative.
    AI_COVERAGE_FLOOR: float = float(_env("POCKET_AI_COVERAGE_FLOOR", "70"))

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    def ensure_dirs(self) -> bool:
        """Create storage dirs; returns False if storage is unusable (honest health)."""
        try:
            (self.STORAGE_DIR / "originals").mkdir(parents=True, exist_ok=True)
            (self.STORAGE_DIR / "processed").mkdir(parents=True, exist_ok=True)
            (self.STORAGE_DIR / "crops").mkdir(parents=True, exist_ok=True)
            (self.STORAGE_DIR / "reports").mkdir(parents=True, exist_ok=True)
            (self.STORAGE_DIR / "bills").mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False


settings = Settings()
