"""Espacio reservado para un proveedor de OpenAI Decisions.

NO está implementado a propósito: no se consultó su documentación y no se
inventan endpoints ni parámetros. Para incorporarlo, implementar Provider
(ver base.py) y registrarlo en classify/__init__.py.
"""
from .base import Item, Provider, ProviderError, Result


class OpenAIDecisionsProvider(Provider):
    name = "openai_decisions"
    label = "OpenAI Decisions (no implementado)"
    external = True

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "available": False, "external": True, "model": None,
                "reason": "Pendiente: adaptador no implementado en este POC.", "description": ""}

    def model(self) -> str:
        return ""

    def classify(self, items: list[Item]) -> tuple[list[Result], int]:
        raise ProviderError("El proveedor OpenAI Decisions no está implementado.")
