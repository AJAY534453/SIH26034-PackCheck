"""Vision provider abstraction.

The platform must never be wired to one vendor, and it must run with no vendor at all:
an inspector on an offline laptop still gets the full on-device OCR pipeline. This module
therefore exposes a single capability — `run_vision_json(prompt, images) -> str | None` —
with an honest status describing whether a provider is configured.

Supported today: Google Gemini (REST, stdlib only — no SDK, so nothing new to install and
nothing to break offline). Adding another vendor means implementing one function here.

Secrets: the API key is read from the server environment (`GEMINI_API_KEY`) and is never
returned to a client, logged, or embedded in a response.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from backend.config import settings


@dataclass
class ProviderStatus:
    enabled: bool
    provider: str
    model: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
        }


def provider_status() -> ProviderStatus:
    """Whether a vision provider is configured. Never raises, never touches the network."""
    if settings.AI_ENABLED.strip().lower() in {"0", "false", "no", "off"}:
        return ProviderStatus(False, settings.AI_PROVIDER, settings.AI_MODEL, "AI vision disabled by configuration (AI_ENABLED=0).")
    if not settings.GEMINI_API_KEY:
        return ProviderStatus(
            False,
            settings.AI_PROVIDER,
            settings.AI_MODEL,
            "No vision provider API key configured — PACKCHECK AI is running the on-device OCR "
            "pipeline only. Set GEMINI_API_KEY to enable vision extraction.",
        )
    return ProviderStatus(True, settings.AI_PROVIDER, settings.AI_MODEL, "Vision provider configured.")


def vision_available() -> bool:
    return provider_status().enabled


def _generate(parts: list[dict], *, json_mode: bool = True) -> str:
    """POST a parts list to the configured provider and return the concatenated text."""
    status = provider_status()
    if not status.enabled:
        raise RuntimeError(status.reason)

    provider = status.provider.lower()
    if provider != "gemini":
        raise RuntimeError(f"Unsupported AI provider '{status.provider}'. Implement it in backend/ai/provider.py.")

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            **({"responseMimeType": "application/json"} if json_mode else {}),
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.AI_MODEL}:generateContent"
        f"?key={settings.GEMINI_API_KEY}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.AI_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Vision provider returned HTTP {exc.code}. {detail}".strip()) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise RuntimeError(f"Vision provider unreachable: {exc.reason}") from exc
    except TimeoutError as exc:  # pragma: no cover - network dependent
        raise RuntimeError("Vision provider timed out.") from exc

    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError(f"Vision provider returned no content{' (blocked: ' + str(blocked) + ')' if blocked else ''}.")
    chunks: list[str] = []
    for part in (candidates[0].get("content") or {}).get("parts") or []:
        text = part.get("text")
        if isinstance(text, str):
            chunks.append(text)
    if not chunks:
        raise RuntimeError("Vision provider returned an empty response.")
    return "\n".join(chunks)


def run_vision_json(prompt: str, images: list[tuple[bytes, str]]) -> str:
    """Send images + prompt to the configured provider and return its raw text.

    Raises RuntimeError with an operator-friendly message on any failure. Images are sent
    as inline base64 data — no public URLs, nothing written to a third-party store by us.
    """
    parts: list[dict] = [{"text": prompt}]
    for data, mime in images:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}})
    return _generate(parts)


def run_text_json(prompt: str) -> str:
    """Text-only provider call (no images), used to re-phrase an already-grounded answer."""
    return _generate([{"text": prompt}])
