# Community Lens

POC local para explorar historiales de comunidades a partir de exportaciones de WhatsApp (`.zip` o `.txt`):
estadísticas reproducibles, búsqueda de texto completo, categorías revisables y evidencia
(mensaje original, contexto y ubicación en el archivo) para cada resultado.

La app tiene dos modos, separados en la barra de navegación:

- **Estadísticas y mensajes**: el motor determinista. Funciona sin ningún LLM y nada sale de la máquina.
- **Periódico**: arma una página para ponerse al día con lo que se compartió en un rango de fechas.
  Usa el modelo de lenguaje que configurés (Claude, GPT, Gemini u otro compatible) para redactar; los
  enlaces, las fechas y las cifras siguen saliendo del chat importado.

La clasificación semántica y el periódico son capas opcionales y nunca calculan los conteos oficiales.

## Arrancar

Requiere Python 3.11+ (con SQLite compilado con FTS5, lo habitual).

```bash
./run.sh
```

Crea `.venv`, instala dependencias la primera vez y sirve la app en **http://127.0.0.1:8765**.

Equivalente manual:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m community_lens
```

Configuración opcional: `cp .env.example .env` y editar. Sin `.env` todo funciona con los valores por defecto.

## Generar el periódico

1. Exportá el chat desde WhatsApp e importalo en **Importar chat**.
2. Poné en `.env` la key del modelo que tengás (ver `.env.example`) y reiniciá la app:
   - `ANTHROPIC_API_KEY` para Claude (modelo por defecto `claude-opus-5-5`), o
   - `OPENAI_API_KEY` + `OPENAI_MODEL` para GPT o cualquier API compatible (`OPENAI_BASE_URL`), o
   - `GEMINI_API_KEY` + `GEMINI_MODEL` para Gemini.
   Se usa lo que tenga key. Con varias, "Automático" redacta con la primera de esa lista y, si ese modelo falla,
   pasa a la siguiente (la edición guardada dice cuál redactó y cuál falló); también se puede fijar uno.
3. En **Generar periódico** elegí el rango (última semana, dos semanas, mes o fechas a mano), revisá qué
   enlaces entran y generá. La edición queda guardada y se puede abrir o descargar como un solo HTML.

Cómo se arma una edición:

| Parte | Quién la produce |
|---|---|
| Qué enlaces entran, fecha en que se compartieron, conteos de mensajes y participantes | Código, desde SQLite |
| Tipo de enlace (artículo, red social, sitio) | Regla fija por dominio y ruta |
| Titulares, resúmenes, cronología, "hilo de la edición" | El modelo, leyendo el texto de cada página |
| Lectura de la conversación (opcional) | El modelo, sobre mensajes seudonimizados |
| Mensajes por tema dentro de esa lectura | Código, contando la agrupación que hizo el modelo |

- El modelo nunca escribe direcciones: se refiere a cada enlace por un número y el código pone la URL.
  Un número que no existe se descarta, así que una edición solo enlaza a lo que se compartió en el chat.
- Las páginas se descargan desde tu máquina y su texto limpio se guarda en la tabla `pages`; las ediciones,
  en `editions`. Si un artículo no se puede leer (muro de pago, enlace caído), entra el siguiente del rango
  y el que falló queda listado en "No se pudo leer".
- De redes sociales solo se obtiene lo que cada plataforma publica para incrustar (texto del post, título
  del video). Lo que no se pudo ver se muestra como "sin vista previa disponible", sin texto inventado.
- La plantilla es fija (`community_lens/newspaper_html.py` y `newspaper.css`): sin scripts ni recursos externos.

### Sin pantalla (para automatizar)

```bash
.venv/bin/python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --dias 7 --salida sitio/
```

Importa la exportación (`.zip` o `.txt`; volver a pasar el mismo archivo no duplica nada), genera la edición de los
últimos 7 días con mensajes y escribe en `sitio/` un `index.html` más una página por edición. Esa carpeta es solo
HTML: se puede subir a cualquier hosting estático (por ejemplo, un repositorio aparte conectado a Vercel).
`--desde/--hasta` fijan el rango y `--solo-publicar` reescribe el sitio sin generar. Sin `--modelo` redacta el primer
proveedor configurado que responda; con `--modelo openai` (o `anthropic`, `gemini`) solo ese.

Las lecturas de la conversación no salen por defecto. Para incluirlas hay que pedirlas y confirmarlo:
`--lecturas todos --confirmo-enviar-chat` (o una lista: `anthropic,openai`). Antes de publicar en un sitio abierto, tené presente que
esas lecturas resumen una conversación privada.

El formato de periódico y la idea de que un modelo lea las páginas y arme la portada vienen de
[ai-newtab](https://github.com/ThariqS/ai-newtab) de Thariq (`ThariqS`, licencia MIT). Aquí no se usa su código:
la lectura se hace en Python y el diseño es una plantilla, para que cada edición salga con la misma forma.

## Pruebas

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Resultado de la última ejecución (2026-10-09): **66 passed**. Usan solo fixtures sintéticos
(`tests/fixtures/`), una base temporal por prueba y ninguna llamada de red.

| Archivo | Qué cubre |
|---|---|
| `test_parser.py` | Multilínea y Unicode, formatos iOS / Android / exportador de terceros, reloj de 12 y 24 h, fechas ambiguas y contradictorias, avisos del sistema, multimedia omitida, eliminados, encuestas, cada línea contabilizada |
| `test_import.py` | Elección obligatoria de fecha ambigua, trazabilidad, reimportación idéntica, repeticiones legítimas, exportaciones solapadas, otro chat no se fusiona, alias, zona horaria, eliminación en cascada |
| `test_resources.py` | Agrupación de documentos por versión (la última es el número más alto, no la primera coincidencia), reenvíos de versiones viejas, enlaces por URL |
| `test_stats_search.py` | Totales y denominadores, meses parciales, enlaces vs. mensajes con enlaces, FTS y filtros, contexto, reporte solo agregado y escapado |
| `test_classify.py` | Reglas (multi-etiqueta, contexto acotado), registro de ejecuciones, correcciones humanas sin sobrescribir, bloqueos de Jev, forma de la solicitud a Jev |
| `test_newspaper.py` | Tipos de enlace, vista previa sin red ni modelos, seudonimización, enlaces solo del chat (ids inventados descartados), consentimiento para lecturas, conteos por tema hechos por código, modo automático con varios modelos, importación de `.zip`, comando de terminal completo |
| `test_contract.py` | Las respuestas reales de la API coinciden con los tipos de TypeScript (sin campos de más ni de menos); el TypeScript compila en modo estricto y `web/dist` está al día |
| `test_api.py` | Flujo completo por HTTP sin Jev, cabeceras de seguridad, frontend (fuentes y compilado) sin `innerHTML`, sin enlaces inventados a WhatsApp, diagrama sin acceso a red |

## Qué probar primero

1. **Importar** → elegí el `.zip` o el `.txt` exportado. Revisá el diagnóstico, el orden de fecha detectado (con su evidencia) y el destino sugerido.
2. **Resumen** → métricas con denominador, actividad por mes/día (los meses parciales salen rayados), autores, dominios.
3. **Recursos** → buscá un documento por nombre: muestra la última versión, las anteriores, quién las compartió y dónde están en la conversación.
4. **Explorar** → buscá un término, filtrá por autor o categoría, abrí **Ver contexto** y probá **Copiar referencia**.
5. **Categorías** → ejecutá *Clasificar con reglas*, entrá a una categoría y corregí un mensaje.
6. **Reporte** → abrí o descargá el HTML/TXT agregado.
7. Reimportá el mismo archivo: no se duplica nada.
8. **Generar periódico** (requiere una key en `.env`) → elegí "Última semana", revisá qué enlaces entran y qué sale de tu máquina, y generá.

## Arquitectura

```
community_lens/
  parser.py      TXT → registros tipados. Sin base de datos, sin LLM.
  importer.py    Vista previa → confirmación, reconciliación de solapes, zona horaria, borrado.
  db.py          Esquema SQLite (+ FTS5).
  stats.py       Estadísticas oficiales en SQL, con definiciones y denominadores.
  search.py      Búsqueda FTS5, vista de contexto, categorías efectivas.
  resources.py   Biblioteca de documentos (agrupados por versión) y enlaces compartidos.
  classify/      Interfaz de proveedores: rules (local), jev (opcional), openai_decisions (reservado).
  report.py      Reporte agregado HTML / texto.
  newspaper.py   Periódico: selección de enlaces, lectura de páginas, seudonimización, armado de la edición.
  newspaper_html.py + newspaper.css   Plantilla fija del periódico (HTML autocontenido).
  llm.py         Modelos de lenguaje sin atarse a un proveedor (Claude, OpenAI o compatible, Gemini).
  cli.py         Periódico desde la terminal: importar, generar y publicar el sitio estático.
  api.py         FastAPI + frontend estático.
web/src/         Frontend en TypeScript estricto (tipos de la API, cliente, DOM, gráficos, vistas).
web/dist/        JavaScript compilado desde web/src; versionado para correr sin Node.
web/vendor/      Observable Plot y D3, copiados localmente (sin CDN).
docs/diagramas/  Diagrama de flujo de datos (fuente JSON + HTML generado con Archify).
tests/           pytest + fixtures sintéticos.
```

### Gráficos de datos (Observable Plot)

Los gráficos del Resumen se dibujan con [Observable Plot](https://observablehq.com/plot/) 0.6.17 sobre
D3 7.9.0, ambos con licencia ISC y copiados en `web/vendor/` para no depender de internet ni de Node.

| Gráfico | Qué muestra | Dato de origen (`/api/stats`) |
|---|---|---|
| Mensajes por mes | Barras; los meses parciales van tenues y con asterisco | `monthly` |
| Calendario diario | Una celda por día, más oscuro = más mensajes | `daily` |
| Cuándo se conversa | Por defecto, barras por día de la semana y por hora; como alternativa, mapa de calor día × hora | `weekday`, `hour`, `weekday_hour` |
| Categorías por mes (pestaña Categorías) | Un gráfico pequeño por categoría: % de los mensajes clasificados de cada mes | `/api/classify/monthly` |

Plot solo dibuja: todos los números siguen saliendo del SQL del backend, y cada gráfico tiene la opción
"Ver como tabla". El calendario se puede enlazar directo con `#/resumen?vista=dia` y el mapa de calor con `#/resumen?ritmo=calor`.

**Por qué Plot y no ECharts.** Se probó Plot primero, con ECharts como alternativa si fallaba. Plot funcionó
bajo la política de seguridad estricta de la app; lo único que necesitó fue autorizar por hash los dos
bloques `<style>` fijos que inserta (`PLOT_STYLE_HASHES` en `api.py`), sin abrir `'unsafe-inline'`. Pesa la
mitad que ECharts (unos 490 KB entre Plot y D3) y genera SVG, que respeta el tema claro/oscuro. ECharts
seguiría siendo la opción si más adelante hiciera falta zoom con arrastre o gráficos muy interactivos.

El reporte exportable no usa Plot: lleva su propio SVG para seguir siendo un único archivo HTML sin scripts.

### Diagrama del flujo de datos (Archify)

El recorrido completo, del TXT al reporte, está en un diagrama interactivo:

- Con la app corriendo: **http://127.0.0.1:8765/como-funciona** (enlace "Cómo funciona ↗" en la barra superior y en Resumen).
- Sin la app: abrir `docs/diagramas/flujo-de-datos.html` en el navegador.

Está hecho con [Archify](https://github.com/tt-a1i/archify) (v3.0.1, commit `73aaa06`, licencia MIT), una
herramienta que convierte una descripción en JSON en un diagrama HTML autocontenido con zoom, trazado de
rutas, tema claro/oscuro y exportación.

**Qué tipo usamos y por qué.** Archify ofrece cinco tipos de diagrama: `architecture`, `workflow`,
`sequence`, `dataflow` y `lifecycle`. Usamos **`dataflow`**, porque lo que más importa explicar en esta app
es qué pasa con los datos en cada etapa (Origen → Importar → Guardar → Analizar → Mostrar): cuál es el camino
principal, qué es lectura local, qué es una estimación y cuál es el único flujo que sale de la máquina (Jev).
Los otros tipos no aportaban tanto hoy: `architecture` repetiría el árbol de carpetas de arriba, y
`sequence` / `lifecycle` / `workflow` servirían más adelante para documentar la reconciliación de solapes
o el ciclo de vida de una ejecución de clasificación.

**Para qué no se usa.** Archify dibuja diagramas de sistemas, no gráficos de datos: de esos se encarga
Observable Plot (ver arriba). Tampoco es una dependencia
de ejecución: el HTML generado está versionado y la app solo lo sirve.

**Regenerarlo** después de editar `docs/diagramas/flujo-de-datos.dataflow.json` (requiere Node 18+):

```bash
git clone --depth 1 https://github.com/tt-a1i/archify.git /tmp/archify
node /tmp/archify/archify/bin/archify.mjs validate dataflow docs/diagramas/flujo-de-datos.dataflow.json
node /tmp/archify/archify/bin/archify.mjs render   dataflow docs/diagramas/flujo-de-datos.dataflow.json docs/diagramas/flujo-de-datos.html
```

El periódico tiene su propio diagrama, hecho igual: `docs/diagramas/periodico.html` (y `periodico.png` para
compartir), con fuente en `periodico.dataflow.json`. Muestra qué pone el código y qué el modelo, qué sale de
la máquina y qué pasos son todavía manuales. En la app se abre con "Cómo se arma ↗", junto a "Generar periódico" (`/como-funciona/periodico`).

Los diagramas no contienen datos de ningún chat. Como llevan scripts en línea, la app sirve el suyo con una política
propia que le impide hacer conexiones de red o llamar a la API (`connect-src 'none'`); el resto de la app
conserva su política estricta sin scripts en línea.

### Frontend en TypeScript

El frontend está escrito en TypeScript con la configuración más estricta (`strict`, `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, entre otras) y sin framework ni empaquetador: `tsc` compila cada módulo de
`web/src/` a `web/dist/`, y el navegador los carga como módulos ES.

| Módulo | Contenido |
|---|---|
| `types.ts` | El contrato de la API: un tipo por cada respuesta del backend |
| `api.ts` | Cliente: cada función fija la ruta junto con su tipo de respuesta |
| `dom.ts` | Construcción del DOM; el contenido de los chats solo puede entrar como texto |
| `charts.ts` | Gráficos con Observable Plot |
| `format.ts` | Formato de números, fechas y etiquetas |
| `app.ts` | Vistas y enrutamiento |

**Para correr la app no hace falta Node**: `web/dist/` está versionado. Node (18+) solo se necesita para
modificar el frontend:

```bash
npm install          # una vez
npm run check        # regenera el esquema de la API y compila web/src → web/dist
npm run typecheck    # solo verificar tipos
```

**Cómo se garantiza que los tipos sean correctos.** Los tipos de `types.ts` no se dan por buenos:
`npm run schema` genera `web/src/api-schema.json` a partir de ellos, y `tests/test_contract.py` valida las
respuestas reales de cada endpoint contra ese esquema, que no admite campos de más ni de menos. Si alguien
cambia una respuesta en Python sin actualizar `types.ts` (o al revés), esa prueba falla. La misma prueba
verifica que el TypeScript compile y que `web/dist/` y el esquema estén al día con las fuentes.

No se usó React: para cinco vistas sin estado compartido complejo no aportaba claridad, y habría obligado a
sumar un empaquetador. Migrar a React más adelante no toca el backend ni `types.ts` / `api.ts`.

### Modelo de datos

| Tabla | Rol |
|---|---|
| `chats` | Un grupo, con su zona horaria |
| `imports` | Un archivo incorporado a un chat: SHA-256, orden de fecha (detectado o elegido), zona declarada, diagnóstico y resultado de reconciliación |
| `import_issues` | Líneas que no se pudieron convertir en registro; nunca se descartan |
| `authors` | Identificador tal como aparece (alias). `canonical_id` guarda una reconciliación manual |
| `messages` | ID interno, referencia `CL-…`, chat, autor original, hora del archivo / UTC / local, texto original, tipo, posición en la secuencia |
| `message_sources` | En qué archivo y líneas aparece cada mensaje (uno por importación que lo contiene) |
| `links`, `messages_fts` | URLs con dominio; índice de texto completo |
| `analysis_runs` | Ejecución de clasificación: proveedor, modelo, versión de taxonomía, configuración, fechas, estado |
| `classifications` | Predicción original por mensaje y categoría (con puntuación solo si el proveedor la devuelve) |
| `human_labels` | Correcciones humanas, solo-agregado; la más reciente rige y nunca pisa la predicción |

### Parser

- Formatos: `[fecha, hora] Autor: texto` (iOS), `fecha, hora - Autor: texto` (Android) y la variante de exportadores de terceros con `- aviso` y `<image omitted>`.
- **Orden de fecha**: se busca evidencia (un campo con valores mayores que 12). Si no hay, la UI exige elegir; si es contradictoria, lo avisa y los registros que no encajan quedan listados como fecha inválida. La pista de orden cronológico se muestra, pero no decide.
- **Tipos de registro**: `message` (participante), `system` (aviso) y `unrecognized` (parece aviso pero no coincide con ningún patrón conocido: queda marcado para revisión y no cuenta como participación).
- Un marcador de multimedia solo cuenta si tiene evidencia estructural (precedido por U+200E, entre `<…>`, o es todo el cuerpo): alguien que escribe la frase "image omitted" no genera un adjunto.

### Importaciones repetidas y solapadas

- Mismo archivo (mismo SHA-256) → no se hace nada.
- Otro archivo del mismo chat → se alinean las dos **secuencias** dentro del período compartido (`difflib.SequenceMatcher` sobre hora + autor + texto normalizado):
  - coincidencias en secuencia: solo se agrega una fuente adicional;
  - solo en el archivo nuevo: se agregan;
  - solo en lo ya guardado: se conservan, nunca se borra;
  - tramos que difieren en el mismo instante (p. ej. un mensaje editado entre exportaciones): se conservan ambos y quedan marcados como `solape_ambiguo`.
- Dos mensajes idénticos en el mismo segundo son dos mensajes: tres en el archivo nuevo contra dos guardados agrega uno.
- **Actualización vs. otro chat**: la vista previa mide cuántos registros del tramo compartido coinciden y sugiere "actualizar" o "grupo nuevo". Si se elige un chat con el que casi nada coincide, la importación se rechaza salvo confirmación explícita.

## Definiciones de métricas

La UI y la API (`/api/stats` → `definitions`) muestran estas definiciones junto a cada cifra.

| Métrica | Definición | Denominador |
|---|---|---|
| Mensajes de participantes | Registros escritos por un identificador de autor, incluidos multimedia omitida, encuestas y eliminados | Registros totales |
| Avisos del sistema | Entradas, salidas, códigos de seguridad, cambios del grupo. **No cuentan como participación** | — |
| Autores activos | Identificadores distintos con al menos un mensaje en el período (tras reconciliación manual de alias) | — |
| Días activos | Días calendario con al menos un mensaje de participante | Días de conversación |
| Días de conversación | Días desde el primer hasta el último mensaje de participante del chat, recortados por el período. No cuenta los días previos en que el grupo existía pero solo hay avisos | — |
| Promedio por día | Mensajes de participantes ÷ días de conversación (incluye los días sin mensajes) | Días de conversación |
| Promedio por mes | Solo meses completos; los parciales se excluyen para no bajar el promedio artificialmente | Meses completos del período |
| Promedio por hora | Mensajes ÷ (días de conversación × 24), contando madrugadas; se acompaña de la hora pico y su promedio diario | Horas del tramo de conversación |
| Días sin mensajes | Días de conversación sin ningún mensaje de participante; se listan con su día de la semana | Días de conversación |
| Concentración top 5 / top 10 | % de mensajes escritos por los N identificadores con más mensajes. Volumen, no calidad | Mensajes de participantes |
| Mensajes con enlaces | Mensajes con al menos una URL | Mensajes de participantes |
| Enlaces en total | URLs encontradas; un mensaje con tres suma tres | — |
| Dominios | Por dominio: mensajes distintos que lo mencionan y cantidad de apariciones | — |
| Multimedia omitida | Mensajes con marcador de adjunto no incluido en el TXT | Mensajes de participantes |
| Día de semana / hora | Mensajes de participantes por hora local de la zona del chat | Mensajes de participantes |
| Mes parcial | Mes que el historial o el filtro no cubren completo; se señala y no es comparable | — |

La tabla de identificadores incluye **% acumulado** (esa fila más todas las anteriores) y cuántos identificadores
hacen falta para llegar a la mitad de los mensajes. Generaliza el top 5 / top 10 y describe reparto de volumen,
no importancia. Se probó mostrarlo como curva y se descartó por ser menos claro que la tabla; el dato sigue en
`/api/stats` (`concentration.curve`, `half_rank`).

**Categorías por mes** usa como denominador los mensajes *clasificados* de ese mes en la ejecución vigente,
no todos los mensajes: una ejecución de Jev cubre como máximo `JEV_MAX_MESSAGES`, y la UI avisa cuando un mes
tiene menos de la mitad clasificada. Son estimaciones (con las correcciones humanas aplicadas), no conteos oficiales.

No se calcula "miembros silenciosos": el TXT no trae la lista de miembros.

## Clasificación

Categorías (`taxonomía 2026-10-v1`): `pregunta`, `oferta_de_colaboracion`, `propuesta_de_proyecto`,
`recurso_compartido`, `anuncio`, `conversacion_general`. Un mensaje puede tener varias.

- **Reglas (`rules`)** — disponible siempre. Patrones en español e inglés más un contexto acotado
  (mensajes previos dentro de 15 minutos). No devuelve puntuaciones; la evidencia es el nombre de la regla.
  Es una heurística: sirve para encontrar candidatos, no para medir.
- **Jev de TypeSafe (`jev`)** — opcional. Ver abajo.
- **OpenAI Decisions** — solo un espacio reservado (`classify/openai_decisions.py`), sin implementar.

**Ejecución vigente.** Cada clasificación se guarda como una ejecución y ninguna borra a otra. Una sola es la
"vigente" por chat: la que alimenta las categorías en Resumen, Explorar y el reporte. Una ejecución nueva pasa a
ser la vigente solo si clasificó al menos tantos mensajes como la actual; así una corrida parcial (el tope de Jev,
un período acotado) no tapa una completa. Se cambia a mano en Categorías → "Ejecuciones guardadas" → "Usar esta".

Cada resultado en la UI indica su origen (`reglas`, `Jev` o `revisado`). Las correcciones humanas
se guardan aparte y la predicción original sigue visible.

### Estado de Jev

**Conectado y en uso.** Con `TYPESAFE_API_KEY` y `JEV_ENABLED=1` en `.env`, el adaptador responde contra el
servicio real (modelo `jev-1.13.0`). Las pruebas automatizadas usan un transporte falso y no tocan la red.

El adaptador sigue la documentación oficial consultada el 2026-10-07
([API](https://docs.typesafe.ai/api.md), [Noul](https://docs.typesafe.ai/primitives/noul.md),
[Modelos](https://docs.typesafe.ai/models.md)): `POST https://api.typesafe.ai/v1/systemone`,
cabecera `Authorization: Bearer`, cuerpo `{model, state, questions}` con una pregunta `noul` por categoría,
respuesta `answers.<id>.noul` entre 0 y 1. Antes de confiar en él, hacé una primera ejecución pequeña
y compará contra etiquetas propias.

Para activarlo, en `.env`:

```
JEV_ENABLED=1
TYPESAFE_API_KEY=...
```

Además, cada ejecución pide confirmación en la UI. Qué se envía: el texto del mensaje (hasta 4000
caracteres) y el de hasta `JEV_CONTEXT_MESSAGES` mensajes previos. No se envían identificadores de autor,
fechas ni nombre del grupo; lo que esté escrito dentro del texto sí viaja tal cual. Tope por ejecución:
`JEV_MAX_MESSAGES` (200).

Sobre la puntuación: se guarda el valor `noul` tal como llega y la categoría se asigna si supera
`JEV_THRESHOLD` (0,8). TypeSafe indica que la mejor exactitud es en inglés; con chats en español no está
medida, así que no debe leerse como probabilidad calibrada.

## Privacidad y seguridad

- Escucha solo en `127.0.0.1`. No hay autenticación: no cambiés `CL_HOST` en una red compartida.
- En el modo estadísticas, ningún dato sale de la máquina salvo que actives Jev y confirmes cada ejecución.
- Generar el periódico sí envía datos al proveedor del modelo que hayás configurado: siempre el texto público de las páginas enlazadas; el texto del chat solo si marcás una "lectura de la conversación" y confirmás la casilla. En ese caso los mensajes van con seudónimos (P01, P02…) y sin teléfonos, correos ni menciones; un nombre de pila escrito dentro de un mensaje sí puede ir. La pantalla muestra cuántos mensajes se enviarían antes de generar, y cada edición guarda qué se envió y a quién.
- Al descargar páginas enlazadas se rechazan direcciones que no sean públicas, para que un enlace del chat no alcance la red local.
- Las keys de los modelos se leen de `.env`, no se registran y nunca llegan al navegador.
- `.gitignore` excluye `Chats/`, `data/`, bases SQLite, `reports/` y `.env`.
- Los logs registran conteos y estados, nunca texto de mensajes ni secretos; el log de accesos HTTP está desactivado porque las URLs de búsqueda contienen términos del chat.
- El contenido del chat se trata como dato: el frontend solo lo inserta como texto (sin `innerHTML`; el tipo `Child` de `dom.ts` no admite HTML) y la app envía una Content-Security-Policy sin scripts en línea; los únicos estilos en línea permitidos son dos bloques fijos de la librería de gráficos, autorizados por hash.
- **Eliminar grupo y datos** (pestaña Importar) borra mensajes, índice, enlaces, clasificaciones y correcciones, y compacta la base. Tus TXT originales no se tocan.
- La vista previa de importación guarda una copia temporal en `data/staging/`; se borra al confirmar, al arrancar la app y pasada una hora.

## Implementado

- Importación de TXT con diagnóstico, detección de fecha con evidencia y selección obligatoria si es ambigua.
- Zona horaria por chat (inicial `America/Costa_Rica`), editable con recálculo.
- Reimportación idempotente y reconciliación de exportaciones solapadas sin fusiones destructivas.
- Estadísticas por grupo y período con definiciones, denominadores, cobertura y meses parciales.
- Búsqueda FTS5 (sin distinguir tildes, frases, prefijos) con filtros por grupo, fecha, autor, categoría, tipo de registro, enlaces, multimedia y revisión.
- Biblioteca de recursos: documentos agrupados por nombre con su última versión y enlaces agrupados por URL, con búsqueda por nombre, dominio o persona.
- Vista de contexto, referencia copiable con cita y ubicación en el archivo, enlaces internos `#/m/<id>`.
- Clasificación por reglas, registro de ejecuciones, correcciones humanas.
- Reconciliación manual de alias (unir / separar).
- Reporte agregado en HTML y texto; los identificadores de autor solo se incluyen si se pide.
- Eliminación de un chat y sus datos derivados.
- Periódico por rango de fechas: enlaces clasificados por regla fija, páginas leídas desde la máquina, redacción con el modelo configurado (Claude, OpenAI o compatible, Gemini; automático si hay varios), lecturas opcionales de la conversación con seudónimos y confirmación, ediciones guardadas y descargables.
- Importación de `.zip` de WhatsApp y comando de terminal que importa, genera y escribe un sitio estático.
- Diagrama interactivo del flujo de datos ("Cómo funciona"), generado con Archify.
- Gráficos con Observable Plot: barras por mes, calendario diario, mapa de calor día × hora y categorías por mes.

## Pendiente

- Medir la exactitud de Jev en español contra etiquetas propias (la conexión ya está verificada).
- Proveedor OpenAI Decisions.
- Periódico: los proveedores OpenAI y Gemini están implementados pero no se probaron contra el servicio real (solo Claude). Generación en segundo plano con progreso. Editar o quitar una nota antes de compartir. Paso de borrador y aprobación antes de publicar, y una versión pública que deje fuera lo que viene de la conversación. Nada corre programado todavía: exportar el chat y subir el sitio se hacen a mano.
- Importar una exportación "con archivos" sobre una "sin archivos" del mismo chat duplica los mensajes con adjunto (el texto del marcador cambia); por ahora conviene importarla como grupo aparte.
- Importar el formato `chat.md` (hoy `.zip` y `.txt`).
- Más patrones de avisos en español: los archivos reales disponibles estaban en inglés, así que los patrones en español solo se validaron con fixtures sintéticos.
- Clasificación en segundo plano con progreso (hoy la solicitud espera a que termine).
- Pruebas automatizadas de interacción en navegador (clics en formularios; hoy se verifica por tipos, contrato y capturas).
- Seleccionar un rango en el gráfico para fijar el filtro de fechas.
- Vista dedicada para resolver solapes ambiguos (hoy se filtran en Explorar → "Marcados para revisión").

## Limitaciones

- **Periódico:** los resúmenes son automáticos y no se verifican contra los artículos. La regla que separa artículo, red social y sitio es simple (dominio y ruta) y puede clasificar mal un enlace. Las páginas que dependen de JavaScript o de sesión iniciada no se pueden leer.
- **Recursos:** la "última versión" de un documento es el número de versión más alto que aparece en el nombre del archivo dentro de ese chat. No detecta una versión más nueva compartida en otro grupo, como enlace, o con otro nombre; tampoco lista los documentos cuyo nombre no quedó en la exportación. El archivo en sí nunca está en el TXT.
- El TXT no trae IDs nativos, reacciones, respuestas citadas, adjuntos ni lista de miembros. No existen enlaces para abrir un mensaje en WhatsApp.
- Las horas son las del teléfono que exportó. Si dos exportaciones del mismo chat vienen de zonas distintas, hay que declarar la zona correcta de cada una para que se reconcilien.
- La reconciliación compara hora + autor + texto. Si dos teléfonos muestran al mismo autor de forma distinta (nombre de contacto en uno, teléfono en otro), los mensajes no coincidirán: la app lo detecta como baja coincidencia y pide confirmación, pero no los empareja sola.
- Un mensaje editado o eliminado entre dos exportaciones aparece dos veces (marcado como solape ambiguo) hasta que se revise, y mientras tanto suma dos en los conteos.
- El separador autor/texto es el primer `": "`; un nombre de contacto que lo contenga se partiría mal.
- En horas ambiguas por cambio de horario se toma la primera ocurrencia.
- "Autores" son identificadores, no personas. Las estadísticas describen actividad visible en el grupo: no permiten inferir intenciones, quién originó una idea ni incumplimientos, y "sin seguimiento visible" no significa que no hubo seguimiento por privado.
