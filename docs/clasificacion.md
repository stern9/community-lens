# Clasificación

Capa opcional encima de las estadísticas: nunca calcula los conteos oficiales.

Categorías (`taxonomía 2026-10-v1`): `pregunta`, `oferta_de_colaboracion`, `propuesta_de_proyecto`,
`recurso_compartido`, `anuncio`, `conversacion_general`. Un mensaje puede tener varias.

- **Reglas (`rules`)** — disponible siempre y local. Patrones en español e inglés más un contexto acotado
  (mensajes previos dentro de 15 minutos). No devuelve puntuaciones; la evidencia es el nombre de la regla.
  Es una heurística: sirve para encontrar candidatos, no para medir.
- **Jev de TypeSafe (`jev`)** — opcional; envía texto afuera. Ver abajo.
- **OpenAI Decisions** — solo un espacio reservado (`classify/openai_decisions.py`), sin implementar.

## Ejecución vigente, categoría efectiva y correcciones

Las tres cosas se definen en un solo archivo, `community_lens/classify/labels.py`; la búsqueda, el resumen, el
reporte y la API lo consumen.

- **Ejecución vigente.** Cada clasificación se guarda como una ejecución y ninguna borra a otra. Una sola es la
  "vigente" por chat: la que alimenta las categorías en Resumen, Explorar y el reporte. Una ejecución nueva pasa a
  ser la vigente solo si clasificó al menos tantos mensajes como la actual; así una corrida parcial (el tope de
  Jev, un período acotado) no tapa una completa. Se cambia a mano en Categorías → "Ejecuciones guardadas" → "Usar esta".
- **Categoría efectiva.** Manda la corrección humana más reciente; si no hay, o se revirtió, lo que asignó la
  ejecución vigente. Cada resultado en la UI indica su origen (`reglas`, `Jev` o `revisado`).
- **Correcciones.** Se guardan aparte, como historial de solo-agregado: la predicción original sigue visible y
  volver a la clasificación automática también queda registrado.
- **Ejecuciones que fallan.** Una ejecución que termina con cualquier error queda marcada como fallida, sin
  clasificaciones a medias. Si la app se cerró en plena ejecución, se marca como fallida al volver a arrancar.

## Jev

Con `TYPESAFE_API_KEY` y `JEV_ENABLED=1` en `.env`, el adaptador responde contra el servicio real (modelo
`jev-1.13.0`). Las pruebas automatizadas usan un transporte falso y no tocan la red.

El adaptador sigue la documentación oficial consultada el 2026-10-07
([API](https://docs.typesafe.ai/api.md), [Noul](https://docs.typesafe.ai/primitives/noul.md),
[Modelos](https://docs.typesafe.ai/models.md)): `POST https://api.typesafe.ai/v1/systemone`,
cabecera `Authorization: Bearer`, cuerpo `{model, state, questions}` con una pregunta `noul` por categoría,
respuesta `answers.<id>.noul` entre 0 y 1. Antes de confiar en él, hacé una primera ejecución pequeña
y compará contra etiquetas propias.

Además de activarlo en `.env`, cada ejecución pide confirmación en la UI. Qué se envía: el texto del mensaje
(hasta 4000 caracteres) y el de hasta `JEV_CONTEXT_MESSAGES` mensajes previos. No se envían identificadores de
autor, fechas ni nombre del grupo; lo que esté escrito dentro del texto sí viaja tal cual. Tope por ejecución:
`JEV_MAX_MESSAGES` (200).

Sobre la puntuación: se guarda el valor `noul` tal como llega y la categoría se asigna si supera
`JEV_THRESHOLD` (0,8). TypeSafe indica que la mejor exactitud es en inglés; con chats en español no está
medida, así que no debe leerse como probabilidad calibrada.
