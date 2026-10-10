# Cómo contribuir

Gracias por el interés. Community Lens es una prueba de concepto que mantiene una sola persona, así que lo que
más ayuda es un reporte claro o una idea concreta. Antes de proponer código, abrí un issue para acordar el cambio.

## Antes de abrir un issue: no pegués conversaciones reales

La app trabaja con chats privados de otras personas. En un issue, un comentario o un pull request no incluyás
texto de mensajes reales, nombres, teléfonos, capturas de pantalla con conversaciones ni archivos exportados.
Si hace falta un ejemplo, inventalo: los de `tests/fixtures/` muestran el formato. Tampoco pegués keys ni el
contenido de tu `.env`.

## Reportar un error

Revisá primero si ya hay un [issue](../../issues) sobre lo mismo. Si no, contá:

- qué hiciste, qué esperabas y qué pasó (el mensaje de error tal cual, sin datos del chat);
- cómo repetirlo, idealmente con un ejemplo inventado de pocas líneas;
- sistema operativo, versión de Python y, si el archivo viene de WhatsApp, si se exportó desde iOS o Android y
  con o sin archivos.

Un problema de seguridad o de privacidad no va en un issue público: escribile directamente a quien mantiene el
repositorio.

## Proponer una idea

Contá qué problema resuelve y para quién, antes que cómo implementarla. Hay decisiones ya tomadas que conviene
conocer (sin framework en el frontend, SQL sin ORM, nada sale de la máquina sin activación explícita): están en
[AGENTS.md](AGENTS.md).

## Cambiar código

1. Arrancá la app y corré las pruebas como indica el [README](README.md#desarrollo).
2. Leé [AGENTS.md](AGENTS.md): son las reglas del proyecto y valen igual si el cambio lo escribe una persona o
   un agente de código. Si usás un agente, vos respondés por lo que entrega.
3. Hacé un cambio acotado, con su prueba. Antes de entregarlo:
   ```bash
   .venv/bin/python -m pytest -q
   npm run check        # solo si tocaste web/src/
   ```
4. Mensajes de commit: una línea corta con prefijo de tipo (`feature:`, `fix:`, `chore:`).
5. En el pull request, decí qué issue resuelve, qué verificaste y qué no.

Las pruebas no usan la red ni datos reales; un cambio que lo necesite para pasar no se acepta.

## Licencia

El repositorio todavía no tiene una licencia de uso. Hasta que la tenga, consultá antes de reutilizar el código.
