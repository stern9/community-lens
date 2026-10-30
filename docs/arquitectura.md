# Arquitectura

```
community_lens/
  __main__.py    Arranque: sin argumentos levanta la app; `periodico …` corre sin pantalla; `--help`.
  config.py      Configuración: la única lectura del entorno y de .env.
  parser.py      TXT → registros tipados. Sin base de datos, sin LLM.
  importer.py    Vista previa → confirmación, reconciliación de solapes, zona horaria, borrado.
  db.py          Esquema SQLite (+ FTS5).
  stats.py       Estadísticas oficiales en SQL, con definiciones y denominadores.
  search.py      Búsqueda FTS5, vista de contexto, conteos por categoría.
  resources.py   Biblioteca de documentos (agrupados por versión) y enlaces; identidad de un enlace.
  stickers.py    Ranking de stickers por contenido de la imagen, con quién los manda.
  classify/      Proveedores de clasificación: rules (local), jev (opcional), openai_decisions (reservado).
    labels.py    Ejecución vigente, categoría efectiva y correcciones humanas: la única definición.
  report.py      Reporte agregado HTML / texto.
  newspaper/     Periódico.
    __init__.py  Reglas de una edición, selección de enlaces, lectura de páginas, seudonimización, armado y sitio.
    edition.py   Qué es una edición guardada: campos, número de formato, conversión de formas anteriores y estados.
    template.py + newspaper.css   Plantilla fija del periódico (HTML autocontenido).
    brand_icons.py   Logos de las redes sociales (Simple Icons) que dibuja la plantilla.
  dates.py       Fechas en español para el periódico y el reporte.
  llm.py         Modelos de lenguaje sin atarse a un proveedor (Claude, OpenAI o compatible, Gemini).
  cli.py         Periódico desde la terminal: importar, generar y publicar el sitio estático.
  api.py         FastAPI + frontend estático. Los cuerpos de solicitud se validan al entrar.
web/src/         Frontend en TypeScript estricto (ver frontend.md).
web/dist/        JavaScript compilado desde web/src; versionado para correr sin Node.
web/vendor/      Observable Plot y D3, copiados localmente (sin CDN).
docs/diagramas/  Diagramas de flujo de datos (fuente JSON + HTML generado con Archify).
tests/           pytest + datos sintéticos.
```

Principios que atraviesan el código:

- Los conteos oficiales los calcula SQL explícito (sin ORM), nunca un modelo.
- La clasificación y el periódico son capas opcionales encima de eso.
- Lo que toca la red está aislado en un punto por módulo y se puede reemplazar: el transporte de Jev
  (`classify/jev.py`), `llm.Transport` (proveedores de modelos) y `newspaper.Net` (páginas enlazadas). Las pruebas
  reemplazan solo eso.

## Modelo de datos

| Tabla | Rol |
|---|---|
| `chats` | Un grupo, con su zona horaria y su ejecución de clasificación vigente |
| `imports` | Un archivo incorporado a un chat: SHA-256, orden de fecha (detectado o elegido), zona declarada, diagnóstico y resultado de reconciliación |
| `import_issues` | Líneas que no se pudieron convertir en registro; nunca se descartan |
| `authors` | Identificador tal como aparece (alias). `canonical_id` guarda una reconciliación manual |
| `messages` | ID interno, referencia `CL-…`, chat, autor original, hora del archivo / UTC / local, texto original, tipo, posición en la secuencia |
| `message_sources` | En qué archivo y líneas aparece cada mensaje (uno por importación que lo contiene) |
| `links`, `messages_fts` | URLs con dominio; índice de texto completo |
| `sticker_images`, `message_stickers` | Imagen de cada sticker distinto (por SHA-256 del contenido, solo `.webp` verificados) y qué sticker es cada mensaje. Solo los llena un `.zip` con archivos; las imágenes que ningún grupo usa se borran con el grupo |
| `analysis_runs` | Ejecución de clasificación: proveedor, modelo, versión de taxonomía, configuración, fechas, estado |
| `classifications` | Predicción original por mensaje y categoría (con puntuación solo si el proveedor la devuelve) |
| `human_labels` | Correcciones humanas, solo-agregado; la más reciente rige y nunca pisa la predicción |
| `pages` | Texto limpio de cada página enlazada que se leyó para el periódico, bajo la identidad del enlace. No pertenece a un grupo: al eliminar un grupo se borran las que ya ningún otro enlaza |
| `editions` | Ediciones del periódico: rango, quién redactó, qué se envió y el contenido estructurado (`data`, ver periodico.md). `status` (`borrador`, `aprobada`, `descartada` o `heredada`, el valor de las filas anteriores a la aprobación), `approval` (JSON: hash, quién aprobó, por qué canal, cuándo y adónde) y `approved_html` (los bytes que se publican: la versión pública, sin banda, dibujada una vez al aprobar); las tres las agrega `db.connect` a una base vieja |

No hay sistema de migraciones: `db.py` crea lo que falte al conectar y los cambios de esquema son aditivos.
Al abrir una base anterior, también normaliza las claves de la caché `pages` con `resources.link_key`: mueve cada
URL antigua a su clave, o elimina la fila duplicada si esa clave ya existe, conservando la fila normalizada.
Es idempotente y no modifica los enlaces del chat ni las ediciones guardadas.
Dos columnas quedaron de versiones anteriores y se conservan sin uso: `editions.html` y, para comparar,
`messages.match_key` (la clave de reconciliación se recalcula al importar; ver abajo).

## Parser

- Formatos: `[fecha, hora] Autor: texto` (iOS), `fecha, hora - Autor: texto` (Android) y la variante de exportadores de terceros con `- aviso` y `<image omitted>`.
- **Orden de fecha**: se busca evidencia (un campo con valores mayores que 12). Si no hay, la UI exige elegir; si es contradictoria, lo avisa y los registros que no encajan quedan listados como fecha inválida. La pista de orden cronológico se muestra, pero no decide.
- **Tipos de registro**: `message` (participante), `system` (aviso) y `unrecognized` (parece aviso pero no coincide con ningún patrón conocido: queda marcado para revisión y no cuenta como participación).
- Un marcador de multimedia solo cuenta si tiene evidencia estructural (precedido por U+200E, entre `<…>`, o es todo el cuerpo): alguien que escribe la frase "image omitted" no genera un adjunto.
- El separador autor/texto es el primer `": "`; un nombre de contacto que lo contenga se partiría mal.

## Importaciones repetidas y solapadas

- Mismo archivo (mismo SHA-256) en el mismo grupo → no se hace nada. El mismo archivo puede estar en dos grupos.
- Otro archivo del mismo chat → se alinean las dos **secuencias** dentro del período compartido (`difflib.SequenceMatcher` sobre hora + autor + texto normalizado):
  - coincidencias en secuencia: solo se agrega una fuente adicional;
  - solo en el archivo nuevo: se agregan;
  - solo en lo ya guardado: se conservan, nunca se borra;
  - tramos que difieren en el mismo instante (p. ej. un mensaje editado entre exportaciones): se conservan ambos y quedan marcados como `solape_ambiguo`.
- Dos mensajes idénticos en el mismo segundo son dos mensajes: tres en el archivo nuevo contra dos guardados agrega uno.
- **Con y sin archivos.** En el texto normalizado el marcador de adjunto no cuenta («image omitted» en una
  exportación, `<attached: …>` en la otra), así que el mismo mensaje se reconoce en las dos.
- **Bases anteriores.** La clave de lo ya guardado se recalcula desde la hora, el autor y el texto cada vez que se
  importa; no se confía en la clave guardada, que en una base vieja se calculó con otra regla. No hace falta migrar nada.
- **Actualización vs. otro chat**: la vista previa mide cuántos registros del tramo compartido coinciden y sugiere "actualizar" o "grupo nuevo". Si se elige un chat con el que casi nada coincide, la importación se rechaza salvo confirmación explícita.
- En horas ambiguas por cambio de horario se toma la primera ocurrencia.

## Recursos

La "última versión" de un documento es el número de versión más alto que aparece en el nombre del archivo dentro
de ese chat. No detecta una versión más nueva compartida en otro grupo, como enlace o con otro nombre; tampoco
lista los documentos cuyo nombre no quedó en la exportación. El archivo en sí nunca está en el TXT.

## Pruebas

Sin red, con una base temporal por prueba y sin leer el `.env` de quien las corre (`tests/conftest.py`).

| Archivo | Qué cubre |
|---|---|
| `test_parser.py` | Multilínea y Unicode, formatos iOS / Android / exportador de terceros, reloj de 12 y 24 h, fechas ambiguas y contradictorias, avisos del sistema, multimedia omitida, eliminados, encuestas, cada línea contabilizada |
| `test_import.py` | Elección obligatoria de fecha ambigua, trazabilidad, reimportación idéntica (también con el archivo en dos grupos), repeticiones legítimas, exportaciones solapadas, con y sin archivos, claves de una base anterior, otro chat no se fusiona, alias, zona horaria, eliminación en cascada y de las páginas leídas |
| `test_resources.py` | Agrupación de documentos por versión, reenvíos de versiones viejas, identidad de un enlace |
| `test_stickers.py` | Solo stickers `.webp` reales del `.zip`, ranking por contenido, imágenes para mensajes de una exportación sin archivos sin duplicar, el mismo TXT ya importado solo agrega imágenes, borrado que respeta las de otros grupos, endpoint de la imagen |
| `test_stats_search.py` | Totales y denominadores, meses parciales, enlaces vs. mensajes con enlaces, FTS y filtros, contexto, reporte solo agregado y escapado |
| `test_classify.py` | Reglas, registro de ejecuciones, correcciones humanas sin sobrescribir, clasificados iguales en resumen y mensual, ejecución que falla no queda en curso, bloqueos y forma de la solicitud de Jev |
| `test_newspaper.py` | Tipos de enlace, vista previa sin red ni modelos, seudonimización, enlaces solo del chat, consentimiento para lecturas, modo automático, `.zip`, línea de comandos (rango por defecto, grupos homónimos, validación, `--continuar`, códigos de salida, carpeta, salida JSON), ediciones guardadas con el formato anterior; borrador, aprobación y publicación: base anterior a las columnas de estado, banda de borrador, bytes aprobados frente a una plantilla nueva, integridad dañada (también con un registro de aprobación incompleto o ilegible), registro y validación de la aprobación, descartar y recuperar, borrador pendiente, todo `INSERT` nombra `status`; en el comando: `--aprobar` al generar y con `--edicion` (sin modelo ni red), quién, canal y destino rechazados con código 2 antes de importar, `--destino`, edición de otro grupo, `--descartar` y `--recuperar`, borrador pendiente que frena `--continuar`, `--borrador`, `--listar`, JSON de las acciones y aprobada dañada con código 1; versión pública: nada del sitio lleva la lectura, el nombre del grupo ni "En números" (frase y nombre centinela), `?publico=1` combinado con `?borrador=1` y sus nombres de archivo, rejilla de una columna sin cronología, heredada con lecturas publicada sin ellas, aprobadas antes de la versión pública (con y sin lecturas) fuera del sitio hasta reaprobarlas, bytes con lecturas salteados aunque digan `public`, `--borrador` como versión pública con banda igual a `?publico=1&borrador=1`, y los motivos de reaprobación en `--listar` |
| `test_newspaper_net.py` | Descarga de páginas: direcciones no públicas, cada salto de una redirección, límite de tamaño, errores, fichas de redes sociales |
| `test_llm.py` | Qué se envía a Anthropic, OpenAI y Gemini, traducción de errores sin exponer la key, respuestas mal formadas |
| `test_contract.py` | Respuestas y cuerpos de solicitud coinciden con los tipos de TypeScript; el TypeScript compila en modo estricto y `web/dist` está al día; la vista del periódico compilada, con API y DOM de mentira (`tests/frontend/periodico.mjs`): fechas borradas, estados de las ediciones guardadas (también las aprobadas que esperan reaprobación por la versión pública), enlace "Versión pública", descartadas ocultas y aprobar desde la fila |
| `test_api.py` | Flujo completo por HTTP, validación de cuerpos (422, nunca 500), cabeceras de seguridad, frontend sin `innerHTML`, sin enlaces inventados a WhatsApp, diagrama sin acceso a red |
| `test_origin.py` | Solo se atiende el `Host` de la instancia (puerto, mayúsculas, punto final, nombres que contienen uno permitido, ausente o repetido, `CL_HOST` fuera de loopback); nada modifica con `Origin` o `Sec-Fetch-Site` ajenos; todas las rutas registradas, sin dejar rastro, y desde la propia app las que modifican sí cambian algo (aprobar, descartar y recuperar una edición incluidas) |
| `test_config.py`, `test_main.py` | La configuración sale de un solo lugar y no lee `.env` en pruebas; `--help` y argumentos desconocidos no arrancan nada |
