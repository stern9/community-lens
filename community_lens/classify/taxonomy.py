"""Taxonomía versionada. Cambiar definiciones exige cambiar la versión."""
TAXONOMY_VERSION = "2026-10-v1"

# clave → (etiqueta, definición en español, pregunta sí/no para proveedores semánticos)
CATEGORIES = {
    "pregunta": (
        "Pregunta",
        "Pide información, ayuda, opinión o recomendación al grupo.",
        "Is the author of the message asking the group a question or requesting help, information or a recommendation?",
    ),
    "oferta_de_colaboracion": (
        "Oferta de colaboración",
        "Ofrece tiempo, ayuda, conocimiento, contactos o recursos propios a otros.",
        "Is the author of the message offering their own help, time, skills, contacts or resources to others?",
    ),
    "propuesta_de_proyecto": (
        "Propuesta de proyecto",
        "Propone iniciar o construir algo en conjunto (proyecto, evento, iniciativa).",
        "Is the author of the message proposing that the group start or build a project, event or initiative together?",
    ),
    "recurso_compartido": (
        "Recurso compartido",
        "Comparte un enlace, documento, herramienta o referencia.",
        "Is the author of the message sharing a resource such as a link, document, tool or reference?",
    ),
    "anuncio": (
        "Anuncio",
        "Informa sobre un evento, fecha, convocatoria, lanzamiento o novedad.",
        "Is the message an announcement of an event, date, call for participation, launch or piece of news?",
    ),
    "conversacion_general": (
        "Conversación general",
        "Charla, reacción, saludo u opinión sin las funciones anteriores.",
        "Is the message general conversation, such as chatter, a reaction, a greeting or an opinion, rather than a "
        "question, offer, proposal, shared resource or announcement?",
    ),
}
CATEGORY_KEYS = list(CATEGORIES)


def public() -> list[dict]:
    return [{"key": k, "label": v[0], "definition": v[1]} for k, v in CATEGORIES.items()]
