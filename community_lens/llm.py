"""Modelos de lenguaje para el periódico, sin atarse a un proveedor.

Basta con poner una key en .env para activar ese proveedor. Si hay varias, cada una puede dar su
propia lectura. Ningún modelo calcula cifras: solo redacta a partir del texto que se le pasa.

  ANTHROPIC_API_KEY (+ ANTHROPIC_MODEL)                 Claude, con el SDK oficial
  (cada proveedor acepta además *_LABEL: el nombre con que firma su lectura, p. ej. "Opus 5.5")
  OPENAI_API_KEY + OPENAI_MODEL (+ OPENAI_BASE_URL)     GPT o cualquier API compatible con OpenAI
  GEMINI_API_KEY + GEMINI_MODEL                         Gemini
"""
from __future__ import annotations

import http.client
import json
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
    s = settings()
    out = []
    if s.anthropic_api_key:
        out.append(Provider("anthropic", s.anthropic_label or "Claude", s.anthropic_model or "claude-opus-5-5", s.anthropic_api_key))
    if s.openai_api_key and s.openai_model:
        out.append(Provider("openai", s.openai_label or "OpenAI", s.openai_model, s.openai_api_key,
                            (s.openai_base_url or "https://api.openai.com/v1").rstrip("/")))
    if s.gemini_api_key and s.gemini_model:
        out.append(Provider("gemini", s.gemini_label or "Gemini", s.gemini_model, s.gemini_api_key))
    return out


def missing_model() -> list[str]:
    """Keys presentes a las que les falta el nombre del modelo (se avisa en la UI)."""
    s = settings()
    return [label for label, key, model in (("OpenAI", s.openai_api_key, s.openai_model), ("Gemini", s.gemini_api_key, s.gemini_model))
            if key and not model]


def get(name: str) -> Provider:
    for p in providers():
        if p.name == name:
            return p
    raise LLMError(f"El proveedor «{name}» no está configurado en .env.")


class Transport:
    """Lo único que toca la red al consultar un modelo. Las pruebas pasan uno falso (como el transporte de
    classify/jev.py), así todo lo demás —armar la solicitud, traducir errores, validar la respuesta— se prueba."""

    def open(self, req: urllib.request.Request):
        """Envía una solicitud armada a mano (OpenAI y compatibles, Gemini)."""
        return urllib.request.urlopen(req, timeout=TIMEOUT)

    def sdk_options(self) -> dict:
        """Opciones extra para el cliente del SDK de Anthropic (las pruebas le dan su propio cliente HTTP)."""
        return {}


TRANSPORT = Transport()


def _post(t: Transport, url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with t.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        # Solo el código: el cuerpo del error podría repetir la key o el texto enviado.
        raise LLMError(f"El proveedor respondió HTTP {err.code}.") from None
    except (OSError, http.client.HTTPException, ValueError):  # sin conexión, corte a mitad de la respuesta, JSON inválido
        raise LLMError("No se pudo conectar con el proveedor o la respuesta no fue válida.") from None


def _anthropic(p: Provider, system: str, user: str, t: Transport) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=p.key, timeout=TIMEOUT, **t.sdk_options())
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


def _openai(p: Provider, system: str, user: str, t: Transport) -> str:
    body = _post(t, p.base_url + "/chat/completions", {"Authorization": f"Bearer {p.key}"},
                 {"model": p.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = None
    if not isinstance(text, str):
        raise LLMError("La respuesta de OpenAI no tiene la forma esperada.")
    return text


def _gemini(p: Provider, system: str, user: str, t: Transport) -> str:
    body = _post(t, f"https://generativelanguage.googleapis.com/v1beta/models/{p.model}:generateContent", {"x-goog-api-key": p.key},
                 {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}]})
    try:
        texts = [part.get("text", "") for part in body["candidates"][0]["content"]["parts"]]
    except (KeyError, IndexError, TypeError, AttributeError):
        texts = [None]
    if not all(isinstance(x, str) for x in texts):
        raise LLMError("La respuesta de Gemini no tiene la forma esperada.")
    return "".join(texts)


_CALL = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}


def complete(p: Provider, system: str, user: str, transport: Transport | None = None) -> str:
    """Una pregunta, una respuesta en texto."""
    return _CALL[p.name](p, system, user, transport or TRANSPORT).strip()


def complete_json(p: Provider, system: str, user: str, transport: Transport | None = None) -> dict:
    """Como complete(), pero exige un objeto JSON (tolera un bloque ```json alrededor)."""
    text = complete(p, system, user, transport)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError(f"{p.label} no devolvió JSON válido.") from None
    if not isinstance(data, dict):
        raise LLMError(f"{p.label} no devolvió un objeto JSON.")
    return data
