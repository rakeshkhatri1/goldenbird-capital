"""
Minimal Gemini API client (Google AI generateContent).

Env:
  GEMINI_API_KEYS — comma-separated keys (rotated on quota errors)
  GEMINI_API_KEY  — single key fallback
  GEMINI_MODEL    — primary model (default gemini-2.5-flash-lite)
  GEMINI_MODEL_FALLBACKS — comma list tried after 429/503 on primary
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# gemini-2.0-flash often shows free_tier limit: 0 on new keys; lite/2.5 work.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
DEFAULT_FALLBACKS = "gemini-flash-lite-latest,gemini-2.5-flash,gemini-2.0-flash-lite"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def load_api_keys() -> list[str]:
    raw = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or ""
    return [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]


def load_model_chain(primary: str | None = None) -> list[str]:
    primary = (primary or DEFAULT_MODEL).strip()
    raw = os.environ.get("GEMINI_MODEL_FALLBACKS", DEFAULT_FALLBACKS)
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()]
    chain: list[str] = []
    for m in [primary, *fallbacks]:
        if m and m not in chain:
            chain.append(m)
    return chain


class GeminiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        model: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.model = model


class GeminiClient:
    def __init__(
        self,
        *,
        model: str | None = None,
        keys: list[str] | None = None,
        models: list[str] | None = None,
    ):
        self.models = models if models is not None else load_model_chain(model)
        self.keys = keys if keys is not None else load_api_keys()
        self._key_idx = 0
        self.last_model_used: str | None = None

    def _next_key(self) -> str:
        if not self.keys:
            raise GeminiError(
                "Missing GEMINI_API_KEY or GEMINI_API_KEYS in .env",
                retryable=False,
            )
        key = self.keys[self._key_idx % len(self.keys)]
        self._key_idx += 1
        return key

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_output_tokens: int = 2048,
    ) -> str:
        if not prompt.strip():
            raise GeminiError("Empty prompt", retryable=False)

        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        errors: list[str] = []
        for model in self.models:
            for _ in range(len(self.keys)):
                key = self._next_key()
                try:
                    text = self._request(model, key, body)
                    self.last_model_used = model
                    return text
                except GeminiError as e:
                    short = _short_error(e)
                    errors.append(f"{model} (key …{key[-6:]}): {short}")
                    if e.status in (429, 503):
                        continue
                    raise
        hint = (
            "All models/keys failed. gemini-2.0-flash often has free-tier limit 0 — "
            "set GEMINI_MODEL=gemini-2.5-flash-lite in .env or add billing at ai.google.dev."
        )
        raise GeminiError(f"{hint}\n\n" + "\n".join(errors[-5:]), retryable=False)

    def _request(self, model: str, key: str, body: dict) -> str:
        url = f"{API_BASE}/{model}:generateContent?key={key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return _extract_text(payload)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            raise GeminiError(
                f"Gemini HTTP {e.code}: {detail}",
                status=e.code,
                retryable=e.code in (429, 500, 503),
                model=model,
            ) from e
        except urllib.error.URLError as e:
            raise GeminiError(f"Network error: {e.reason}", retryable=True, model=model) from e


def _short_error(err: GeminiError) -> str:
    msg = str(err)
    if "limit: 0" in msg:
        return "quota limit 0 on this model (try GEMINI_MODEL=gemini-2.5-flash-lite)"
    if len(msg) > 120:
        return msg[:120] + "…"
    return msg


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        block = payload.get("promptFeedback") or payload
        raise GeminiError(f"No candidates in response: {block}", retryable=False)
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        raise GeminiError("Empty text in Gemini response", retryable=False)
    return text
