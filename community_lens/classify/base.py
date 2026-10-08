"""Interfaz común de proveedores de clasificación.

Para agregar un proveedor: implementar Provider y registrarlo en
classify/__init__.py. El resto de la aplicación no cambia.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextMessage:
    text: str
    same_author: bool
    is_question: bool = False


@dataclass
class Item:
    message_id: int
    text: str
    has_link: bool = False
    media_kind: str | None = None
    is_poll: bool = False
    context: list[ContextMessage] = field(default_factory=list)  # mensajes previos, del más antiguo al más reciente


@dataclass
class Result:
    message_id: int
    assigned: list[str]
    scores: dict[str, float] | None = None  # solo si el proveedor los devuelve
    evidence: dict | None = None


class ProviderError(Exception):
    """Fallo del proveedor. El mensaje nunca incluye texto de chats ni secretos."""


class Provider:
    name: str = ""
    label: str = ""
    external: bool = False  # ¿envía texto fuera de esta máquina?

    def info(self) -> dict:
        raise NotImplementedError

    def model(self) -> str:
        raise NotImplementedError

    def config(self) -> dict:
        """Configuración que se guarda con la ejecución. Sin secretos."""
        return {}

    def classify(self, items: list[Item]) -> tuple[list[Result], int]:
        """Devuelve (resultados, cantidad de errores). No inventa resultados para los fallidos."""
        raise NotImplementedError
