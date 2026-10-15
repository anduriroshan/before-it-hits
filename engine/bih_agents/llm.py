"""The one place this product talks to a language model.

Two rules hold everywhere above this module:

  - A model never produces a figure. Every number in anything a user reads comes from the engine,
    and that is enforced mechanically in `verifier.py` rather than asked for in a prompt.
  - A model failing degrades the product, it never breaks it. Every caller must be able to say
    "the briefing is unavailable" and still show the screening result.

Models are named by role rather than by version, and both names are environment-overridable: the
hosted model list moves under you, and a live demo should not depend on one revision still being
served on the day.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

# FAST writes; CAREFUL checks. The checker is the one that must not be fooled, so it is the
# stronger of the two even though it runs on every request.
FAST_MODEL = os.environ.get("BIH_FAST_MODEL", "gemini-3.5-flash")
CAREFUL_MODEL = os.environ.get("BIH_CAREFUL_MODEL", "gemini-3.8-flash")
NARRATOR_MODEL = os.environ.get("BIH_NARRATOR_MODEL", FAST_MODEL)       # the one that writes what people read
TIMEOUT_S = float(os.environ.get("BIH_LLM_TIMEOUT_S", "40"))


class LlmUnavailable(RuntimeError):
    """No usable model: no key, the SDK is absent, or the service refused the request."""


@dataclass
class Reply:
    text: str
    data: dict | None     # parsed JSON when a schema was asked for
    model: str
    ms: int


_client = None


def _client_or_raise():
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise LlmUnavailable("no GEMINI_API_KEY configured")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:                      # serving image without the SDK
        raise LlmUnavailable(f"google-genai is not installed ({exc})") from exc
    try:
        opts = types.HttpOptions(timeout=int(TIMEOUT_S * 1000))
        _client = genai.Client(api_key=key, http_options=opts)
    except Exception:                               # older SDKs take no http options
        _client = genai.Client(api_key=key)
    return _client


def available() -> bool:
    """Whether the agent layer can run at all. Checked once at startup so the UI can say so."""
    try:
        _client_or_raise()
        return True
    except LlmUnavailable:
        return False


def ask(prompt: str, *, model: str = FAST_MODEL, system: str | None = None,
        schema: dict | None = None, temperature: float = 0.0,
        thinking: str = "low", retries: int = 1) -> Reply:
    """One turn. With `schema`, the reply is parsed JSON in `Reply.data`.

    Raises LlmUnavailable rather than returning something empty: a caller that cannot tell a
    refusal from an empty answer will publish the empty answer.
    """
    from google.genai import types
    client = _client_or_raise()

    cfg: dict = {"temperature": temperature}
    if system:
        cfg["system_instruction"] = system
    if schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = schema
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking)
    except Exception:                               # model or SDK without thinking levels
        pass

    last: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            r = client.models.generate_content(
                model=model, contents=prompt, config=types.GenerateContentConfig(**cfg))
            text = (r.text or "").strip()
            if not text:
                raise LlmUnavailable(f"{model} returned nothing")
            data = None
            if schema is not None:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LlmUnavailable(f"{model} returned unparseable JSON: {exc}") from exc
            return Reply(text=text, data=data, model=model,
                         ms=int((time.perf_counter() - t0) * 1000))
        except LlmUnavailable:
            raise
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise LlmUnavailable(f"{model} failed: {type(last).__name__}: {str(last)[:200]}")
