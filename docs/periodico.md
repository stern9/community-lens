# El periódico

Una edición es una página para ponerse al día con lo que la comunidad compartió en un rango de fechas.
El diagrama [`diagramas/periodico.html`](diagramas/periodico.html) muestra el recorrido completo (en la app:
"Cómo se arma ↗", junto a "Generar periódico").

## Quién produce cada parte

| Parte | Quién la produce |
|---|---|
| Qué enlaces entran, fecha en que se compartieron, conteos de mensajes y participantes | Código, desde SQLite |
| Qué cuenta como "el mismo enlace" | Código: una sola regla (`resources.link_key`) |
| Tipo de enlace (artículo, red social, sitio) | Regla fija por dominio y ruta |
| Titulares, resúmenes, cronología, "hilo de la edición" | El modelo, leyendo el texto de cada página |
| Lectura de la conversación (opcional) | El modelo, sobre mensajes seudonimizados |
| Mensajes por tema dentro de esa lectura | Código, contando la agrupación que hizo el modelo |

- **El modelo nunca escribe direcciones.** Se refiere a cada enlace por un número y el código pone la URL. Un
  número que no existe se descarta, así que una edición solo enlaza a lo que se compartió en el chat.
- **Identidad de un enlace.** El mismo enlace compartido con y sin `www.` o `m.`, con o sin barra final, o con
  parámetros `utm_…` es un solo enlace con varias compartidas, tanto en Recursos como en el periódico. Primero se
  agrupa y se cuenta; después se decide el tipo. La dirección que se muestra es la del chat, sin los `utm_…`.
- **Lectura de páginas.** Se descargan desde tu máquina y su texto limpio se guarda en la tabla `pages`, bajo la
  identidad del enlace, para no volver a descargarlas. Si un artículo no se puede leer (muro de pago, enlace caído)
  entra el siguiente del rango y el que falló queda listado en "No se pudo leer".
- **Redes sociales.** Solo se obtiene lo que cada plataforma publica para incrustar (texto del post, título del
  video). Lo que no se pudo ver se muestra como "sin vista previa disponible", sin texto inventado.
- **Plantilla fija** (`community_lens/newspaper_html.py` y `newspaper.css`): un HTML sin scripts ni recursos externos.

## Reglas de una edición

Las aplica `community_lens/newspaper.py`, igual para la pantalla y para la línea de comandos:

- **Rango.** Fechas `AAAA-MM-DD` válidas y en orden. Lo que falte se completa: termina el último día con mensajes
  de participantes (los avisos del sistema posteriores no cuentan) y cubre 7 días contando ese.
- **Artículos.** De 3 a 15 (8 por defecto). Un número fuera de ese rango se rechaza; no se corrige en silencio.
  Se leen hasta 4 artículos de reserva por si alguno falla. Redes sociales: hasta 6. Sitios: hasta 4.
- **Modelos.** "Automático" redacta con el primer proveedor configurado que responda (Claude, OpenAI, Gemini, en ese
  orden) y la edición guarda cuál redactó y cuál falló. Un proveedor elegido por nombre no cae a otro.
- **Lecturas de la conversación.** Envían texto del chat (con seudónimos), así que exigen una confirmación explícita.

## Línea de comandos

```bash
.venv/bin/python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --salida sitio/
.venv/bin/python -m community_lens periodico --help
```

| Opción | Para qué |
|---|---|
| `--archivo` | Exportación a importar antes de generar. Volver a pasar el mismo archivo no duplica nada |
| `--grupo` / `--grupo-id` | Grupo por nombre exacto o por id. Si dos grupos se llaman igual, el comando se detiene y los lista: hay que usar `--grupo-id` |
| `--dias`, `--desde`, `--hasta` | Rango de la edición (ver "Reglas") |
| `--articulos`, `--orden` | Máximo de artículos y prioridad (`recent` o `shared`) |
| `--modelo` | `anthropic`, `openai` o `gemini`; sin esta opción, automático |
| `--lecturas … --confirmo-enviar-chat` | Lecturas de la conversación (`todos` o una lista); sin la confirmación no se envía nada |
| `--salida` | Carpeta del sitio estático: `index.html` y `ediciones/<desde>_a_<hasta>.html` (la edición más reciente de cada rango) |
| `--solo-publicar` | No genera: reescribe el sitio con las ediciones guardadas |

Termina con código 0 si todo salió bien, 1 con un mensaje si algo no se pudo (fecha inválida, grupo ambiguo, sin
modelo configurado…) y 2 si los argumentos no se entienden. Antes de publicar en un sitio abierto, tené presente
que las lecturas resumen una conversación privada.

## Formato guardado

Una edición se guarda como JSON en `editions.data` y se dibuja de nuevo cada vez que se abre o se publica; así una
mejora de la plantilla llega también a las ediciones viejas. `community_lens/edition.py` es el único lugar que
conoce sus campos: los declara, lleva un número de formato (`FORMAT`) y tiene una sola función, `upgrade()`, que
convierte cualquier edición guardada con una forma anterior a la actual. Abrir una edición vieja no la reescribe.
La columna `editions.html`, de una versión anterior, no se lee (ver [arquitectura.md](arquitectura.md#modelo-de-datos)).

El formato de periódico se inspira en ai-newtab (créditos en el [README](../README.md#créditos-y-licencia)); aquí
no se usa su código: la lectura se hace en Python y el diseño es una plantilla, para que cada edición salga con la
misma forma.
