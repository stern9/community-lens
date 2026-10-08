"""Proveedor basado en reglas: expresiones regulares en español e inglés.

Determinista, local y sin credenciales. Es una heurística: reconoce formas
frecuentes, no entiende significado. No devuelve puntuaciones; la evidencia
de cada asignación es el nombre de las reglas que se dispararon.
"""
from __future__ import annotations

import re

from .base import Item, Provider, Result

RULES_VERSION = "rules-1"
F = re.I | re.U

_RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "pregunta": [
        ("signo_interrogacion", re.compile(r"\?|¿", F)),
        ("pedido_de_ayuda", re.compile(
            r"\b(alguien (sabe|conoce|tiene|ha (usado|probado)|puede|podría|me (ayuda|recomienda))|"
            r"me (pueden|podrían|podrian) (ayudar|recomendar|decir|explicar)|qu[eé] recomiendan|"
            r"alguna recomendaci[oó]n|c[oó]mo (hago|puedo|se hace)|does anyone|anyone know|any recommendation)", F)),
    ],
    "oferta_de_colaboracion": [
        ("ofrece_ayuda", re.compile(
            r"\b(me (ofrezco|apunto|anoto|sumo)|cuenten conmigo|cuenta conmigo|(yo )?(puedo|podr[ií]a) "
            r"(ayudar|apoyar|colaborar|aportar|dar una mano|facilitar|compartirles|presentarl[eo]s)|"
            r"con (mucho )?gusto (ayudo|apoyo|colaboro|les ayudo|te ayudo)|estoy (disponible|a la orden|anuente)|"
            r"a la orden (para|si)|si (alguien|alguno) (necesita|ocupa|quiere).{0,40}(me (avisa|escribe|dice)|con gusto)|"
            r"ofrezco|pongo a disposici[oó]n|happy to help|i can help|count me in)", F)),
    ],
    "propuesta_de_proyecto": [
        ("propone", re.compile(
            r"\b(propongo|propuesta(?! de valor)|les propongo|qu[eé] les parece si|y si (hacemos|armamos|creamos|"
            r"organizamos|montamos)|(podr[ií]amos|deber[ií]amos|tenemos que) (armar|crear|hacer|organizar|montar|"
            r"construir|lanzar|desarrollar)|hagamos|armemos|organicemos|construyamos|creemos un|"
            r"ser[ií]a (bueno|genial|interesante|tuanis|chiva) (armar|crear|hacer|organizar|tener)|"
            r"(tengo|traigo) (una|la) idea|idea de proyecto|let'?s build|we should (build|create|organize))", F)),
    ],
    "recurso_compartido": [
        ("comparte_referencia", re.compile(
            r"\b(les (comparto|dejo|paso)|comparto (este|esta|el|la|un|una)|ac[aá] (est[aá]|les dejo|va) (el|la|un|una)|"
            r"(este|esta) (art[ií]culo|paper|repo|repositorio|video|herramienta|curso|gu[ií]a|hilo|podcast))", F)),
    ],
    "anuncio": [
        ("anuncia", re.compile(
            r"\b(les (invito|invitamos|recuerdo|recordamos|aviso|informo|cuento que)|l[oa]s (invito|invitamos|esperamos)|"
            r"est[aá]n (tod[oa]s )?invitad[oa]s|inscripci[oó]n|inscripciones|reg[ií]strese|reg[ií]strate|registro abierto|"
            r"cupos? (limitados?|disponibles?)|save the date|convocatoria abierta|"
            r"(ma[ñn]ana|hoy|este (lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)) a las? \d|"
            r"lanzamos|ya est[aá] disponible|anuncio importante|atenci[oó]n comunidad|recordatorio:)", F)),
    ],
}
_Q = _RULES["pregunta"]
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)


def looks_like_question(text: str) -> bool:
    text = _URL.sub(" ", text)
    return any(p.search(text) for _, p in _Q)


class RulesProvider(Provider):
    name = "rules"
    label = "Reglas (local)"
    external = False

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "available": True, "external": False,
                "model": self.model(), "reason": None,
                "description": "Patrones de texto deterministas. Funciona sin conexión ni credenciales. "
                               "No devuelve puntuaciones."}

    def model(self) -> str:
        return RULES_VERSION

    def config(self) -> dict:
        return {"context_messages": 2, "scores": False}

    def classify_one(self, it: Item) -> Result:
        text = _URL.sub(" ", it.text)  # un "?" dentro de una URL no es una pregunta
        ev: dict[str, list[str]] = {}
        for cat, rules in _RULES.items():
            hits = [n for n, p in rules if p.search(text)]
            if hits:
                ev[cat] = hits
        if it.has_link:
            ev.setdefault("recurso_compartido", []).append("contiene_enlace")
        if it.media_kind == "document":
            ev.setdefault("recurso_compartido", []).append("adjunta_documento")
        if it.is_poll:
            ev.setdefault("pregunta", []).append("encuesta")
        # Contexto acotado: una respuesta breve en primera persona justo después
        # de una pregunta o pedido de OTRA persona se interpreta como ofrecimiento.
        if "oferta_de_colaboracion" not in ev and len(text) <= 160 and it.context:
            prev = it.context[-1]
            if (not prev.same_author and prev.is_question and re.search(
                    r"^\s*(yo(?! (tampoco|no)\b)|con gusto|claro,? (yo|con gusto)|me apunto|aqu[ií] estoy)\b", text, F)
                    and "pregunta" not in ev):
                ev["oferta_de_colaboracion"] = ["respuesta_a_pedido_previo"]
        if not ev:
            ev["conversacion_general"] = ["sin_otra_regla"]
        return Result(it.message_id, list(ev), None, {"rules": ev})

    def classify(self, items: list[Item]) -> tuple[list[Result], int]:
        return [self.classify_one(i) for i in items], 0
