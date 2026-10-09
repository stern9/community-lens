"""Modelos de lenguaje para el periódico, sin atarse a un proveedor.

Basta con poner una key en .env para activar ese proveedor. Si hay varias, cada una puede dar su
propia lectura. Ningún modelo calcula cifras: solo redacta a partir del texto que se le pasa.

  ANTHROPIC_API_KEY (+ ANTHROPIC_MODEL)                 Claude, con el SDK oficial
  (cada proveedor acepta además *_LABEL: el nombre con que firma su lectura, p. ej. "Opus 5.5")
  OPENAI_API_KEY + OPENAI_MODEL (+ OPENAI_BASE_URL)     GPT o cualquier API compatible con OpenAI
  GEMINI_API_KEY + GEMINI_MODEL                         Gemini
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import settings

MAX_OUTPUT_TOKENS = 16000
TIMEOUT = 300


class LLMError(RuntimeError):
    """Fallo al consultar un modelo. El mensaje nunca incluye la key ni el texto enviado."""


@dataclass(frozen=True)
class Provider:
    name: str    # anthropic | openai | gemini
    label: str   # cómo se muestra: "Claude", "OpenAI", "Gemini"
    model: str
    key: str     # nunca se registra ni se devuelve por la API
    base_url: str = ""

    def public(self) -> dict:
        return {"name": self.name, "label": self.label, "model": self.model}


def providers() -> list[Provider]:
    """Proveedores con key y modelo configurados, en orden fijo."""
    settings()  # carga .env
    e = lambda k, d="": os.environ.get(k, d).strip()
    out = []
    if e("ANTHROPIC_API_KEY"):
        out.append(Provider("anthropic", e("ANTHROPIC_LABEL") or "Claude", e("ANTHROPIC_MODEL") or "claude-opus-5-5", e("ANTHROPIC_API_KEY")))
    if e("OPENAI_API_KEY") and e("OPENAI_MODEL"):
        out.append(Provider("openai", e("OPENAI_LABEL") or "OpenAI", e("OPENAI_MODEL"), e("OPENAI_API_KEY"),
                            (e("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")))
    if e("GEMINI_API_KEY") and e("GEMINI_MODEL"):
        out.append(Provider("gemini", e("GEMINI_LABEL") or "Gemini", e("GEMINI_MODEL"), e("GEMINI_API_KEY")))
    return out


def missing_model() -> list[str]:
    """Keys presentes a las que les falta el nombre del modelo (se avisa en la UI)."""
    e = lambda k: os.environ.get(k, "").strip()
    return [p for p, k, m in (("OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL"), ("Gemini", "GEMINI_API_KEY", "GEMINI_MODEL"))
            if e(k) and not e(m)]


def get(name: str) -> Provider:
    for p in providers():
        if p.name == name:
            return p
    raise LLMError(f"El proveedor «{name}» no está configurado en .env.")


def _post(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise LLMError(f"El proveedor respondió HTTP {err.code}.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise LLMError("No se pudo conectar con el proveedor o la respuesta no fue válida.") from None


def _anthropic(p: Provider, system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=p.key, timeout=TIMEOUT)
    try:
        # Fallback del servidor: si un clasificador de seguridad declina, se reintenta en otro modelo.
        res = client.beta.messages.create(
            model=p.model, max_tokens=MAX_OUTPUT_TOKENS, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=[{"role": "user", "content": user}])
    except anthropic.AuthenticationError:
        raise LLMError("La key de Anthropic no es válida.") from None
    except anthropic.RateLimitError:
        raise LLMError("Anthropic: límite de uso alcanzado; probá de nuevo en un momento.") from None
    except anthropic.APIStatusError as err:
        raise LLMError(f"Anthropic respondió HTTP {err.status_code}.") from None
    except anthropic.APIConnectionError:
        raise LLMError("No se pudo conectar con Anthropic.") from None
    if res.stop_reason == "refusal":
        raise LLMError("El modelo declinó la solicitud.")
    if res.stop_reason == "max_tokens":
        raise LLMError("La respuesta del modelo quedó truncada.")
    return "".join(b.text for b in res.content if b.type == "text")


def _openai(p: Provider, system: str, user: str) -> str:
    body = _post(p.base_url + "/chat/completions", {"Authorization": f"Bearer {p.key}"},
                 {"model": p.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("La respuesta de OpenAI no tiene la forma esperada.") from None


def _gemini(p: Provider, system: str, user: str) -> str:
    body = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{p.model}:generateContent", {"x-goog-api-key": p.key},
                 {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}]})
    try:
        return "".join(part.get("text", "") for part in body["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise LLMError("La respuesta de Gemini no tiene la forma esperada.") from None


_CALL = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}


def complete(p: Provider, system: str, user: str) -> str:
    """Una pregunta, una respuesta en texto."""
    return _CALL[p.name](p, system, user).strip()


def complete_json(p: Provider, system: str, user: str) -> dict:
    """Como complete(), pero exige un objeto JSON (tolera un bloque ```json alrededor)."""
    text = complete(p, system, user)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError(f"{p.label} no devolvió JSON válido.") from None
    if not isinstance(data, dict):
        raise LLMError(f"{p.label} no devolvió un objeto JSON.")
    return data
