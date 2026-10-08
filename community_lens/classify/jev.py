"""Adaptador opcional para Jev (TypeSafe).

Implementado según la documentación oficial consultada el 2026-10-07:
  https://docs.typesafe.ai/api.md            (POST /v1/systemone, esquema)
  https://docs.typesafe.ai/primitives/noul.md (preguntas sí/no)
  https://docs.typesafe.ai/models.md          (alias de modelo, idiomas, límites)

Contrato usado (y nada más):
  POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <TYPESAFE_API_KEY> · Content-Type: application/json
  body:     {"model", "state", "questions": {id: {"type": "noul", "instructions"}}}
  response: {"model", "answers": {id: {"type": "noul", "noul": 0..1}}, "usage"}
  errores:  401 clave inválida · 422 validación · 429 límite · 529 sobrecarga

Una pregunta "noul" por categoría permite varias categorías por mensaje.
Si la respuesta no tiene la forma documentada se trata como error: jamás se
completa con valores inventados.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from .base import Item, Provider, ProviderError, Result
from .taxonomy import CATEGORIES

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_STATE_CHARS = 4000
SCORE_MEANING = (
    "Valor 'noul' devuelto por Jev para cada pregunta sí/no (0 a 1): cerca de 1 es un sí fuerte, cerca de 0 un no "
    "fuerte y cerca de 0,5 indecisión. TypeSafe indica que el inglés es el idioma con mejor exactitud; estos chats "
    "están en español y no medimos su calibración, así que no lo leás como probabilidad calibrada.")


def build_request(item: Item, model: str) -> dict:
    """Cuerpo exacto que se envía. Solo texto de mensajes: sin autores, teléfonos de autor, fechas ni nombre del grupo."""
    state = {"message": item.text[:MAX_STATE_CHARS]}
    if item.context:
        state["previous_messages"] = [c.text[:1000] for c in item.context]
    return {
        "model": model,
        "state": state,
        "questions": {k: {"type": "noul", "instructions": v[2] + " Answer about `message` only; "
                          "`previous_messages` are context. The text may be in Spanish."}
                      for k, v in CATEGORIES.items()},
    }


def parse_response(body: dict) -> tuple[dict[str, float], str]:
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise ProviderError("Respuesta de Jev sin 'answers'.")
    scores = {}
    for k in CATEGORIES:
        a = answers.get(k)
        v = a.get("noul") if isinstance(a, dict) else None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ProviderError(f"Respuesta de Jev sin valor 'noul' válido para '{k}'.")
        scores[k] = float(v)
    return scores, str(body.get("model") or "")


class JevProvider(Provider):
    name = "jev"
    label = "Jev (TypeSafe)"
    external = True

    def __init__(self, transport=None):
        self._transport = transport or self._http
        self.resolved_model: str | None = None

    def info(self) -> dict:
        s = settings()
        reason = None
        if not s.jev_enabled:
            reason = "Desactivado: definí JEV_ENABLED=1 en .env para habilitarlo."
        elif not s.jev_api_key:
            reason = "Falta TYPESAFE_API_KEY en el entorno."
        return {
            "name": self.name, "label": self.label, "available": reason is None, "external": True,
            "model": s.jev_model, "reason": reason, "threshold": s.jev_threshold,
            "context_messages": s.jev_context_messages, "max_messages": s.jev_max_messages,
            "score_meaning": SCORE_MEANING,
            "description": "Modelo de decisiones de TypeSafe. Envía texto de mensajes a api.typesafe.ai.",
            "data_sent": (
                f"Por cada mensaje a clasificar se envía a api.typesafe.ai: su texto (hasta {MAX_STATE_CHARS} "
                f"caracteres) y el texto de hasta {s.jev_context_messages} mensajes anteriores como contexto. "
                "No se envían los identificadores de autor, fechas ni el nombre del grupo; pero cualquier nombre, "
                "teléfono o dato personal escrito DENTRO del texto sí se envía tal cual. "
                f"Tope por ejecución: {s.jev_max_messages} mensajes."),
        }

    def model(self) -> str:
        return self.resolved_model or settings().jev_model

    def config(self) -> dict:
        s = settings()
        return {"endpoint": ENDPOINT, "requested_model": s.jev_model, "threshold": s.jev_threshold,
                "context_messages": s.jev_context_messages, "max_messages": s.jev_max_messages,
                "question_type": "noul", "score_kind": "noul", "max_state_chars": MAX_STATE_CHARS}

    def _http(self, payload: dict) -> dict:
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {settings().jev_api_key}", "Content-Type": "application/json"})
        delay = 1.0
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 529) and attempt < 3:
                    time.sleep(delay)  # retroceso exponencial, como pide la documentación
                    delay *= 2
                    continue
                # Solo el código: el cuerpo del error podría repetir texto enviado.
                raise ProviderError({401: "Jev rechazó la clave (401).", 422: "Jev rechazó la solicitud (422)."}
                                    .get(e.code, f"Jev respondió HTTP {e.code}."))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                raise ProviderError(f"No se pudo completar la llamada a Jev ({type(e).__name__}).")
        raise ProviderError("Jev no respondió tras varios intentos.")

    def classify_one(self, it: Item) -> Result:
        s = settings()
        scores, model = parse_response(self._transport(build_request(it, s.jev_model)))
        if model:
            self.resolved_model = model
        return Result(it.message_id, [k for k, v in scores.items() if v >= s.jev_threshold], scores,
                      {"threshold": s.jev_threshold, "context_sent": len(it.context)})

    def classify(self, items: list[Item]) -> tuple[list[Result], int]:
        info = self.info()
        if not info["available"]:
            raise ProviderError(info["reason"])
        results, errors, first_error = [], 0, None

        def work(it):
            try:
                return self.classify_one(it)
            except ProviderError as e:
                return e

        with ThreadPoolExecutor(max_workers=4) as ex:
            for r in ex.map(work, items):
                if isinstance(r, ProviderError):
                    errors += 1
                    first_error = first_error or str(r)
                else:
                    results.append(r)
        if not results and errors:
            raise ProviderError(first_error)
        self.last_error = first_error
        return results, errors
