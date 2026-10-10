# Guía para agentes de código

Leé esto antes de tocar nada. Lo que ya explica el [README](README.md) o `docs/` no se repite acá: se enlaza.
"El dueño" es la persona que corre la app en esta máquina y con quien estás trabajando.

## Qué es y en qué estado está

Community Lens es una app local, de una sola persona, que importa exportaciones de chats de WhatsApp
(`.zip` o `.txt`) a una base SQLite y ofrece dos cosas: estadísticas y búsqueda que no necesitan ningún
modelo ni red, y un "periódico" (una página con lo que se compartió en un rango de fechas) cuyos titulares y
resúmenes redacta el modelo de lenguaje que el usuario configure en `.env` (Claude, OpenAI o compatible, Gemini).

Es una prueba de concepto en uso: quien la corre suele tener en `data/` una base real con las conversaciones
de su comunidad. El flujo al que
apunta (exportar cada cierto tiempo → borrador → aprobación de un administrador → publicar) todavía es manual;
lo que falta está en los [issues del repositorio](https://github.com/stern9/community-lens/issues). No creés, cerrés ni edités issues sin que el dueño
lo pida. No propongás lo que ya está decidido (ver al final).

## Límites que no se negocian

- No abrás, listés ni leás `Chats/`, `data/`, `reports/`, `.env` ni ningún `*.db`: son conversaciones
  privadas reales y credenciales. Para ver comportamiento con datos usá `tests/fixtures/` (sintéticos) y una base
  temporal; `tests/conftest.py` ya trae `do_import`, `FakeNet` y `FakeLLM` para eso.
- No mandés texto de chats ni de páginas a ningún proveedor de modelos salvo que el dueño lo pida en ese
  momento. Una autorización anterior no vale para la siguiente vez: cada envío saca datos de otras personas de
  la máquina. Las pruebas nunca llaman a un proveedor: reemplazan solo el transporte (`llm.Transport`,
  `newspaper.Net`, el de `classify/jev.py`).
- No levantés ni reiniciés la app en el puerto por defecto (8765): ahí suele estar corriendo la instancia del
  dueño sobre su base real. Preferí `pytest` y el `TestClient`. Si hace falta verla en vivo, usá otro puerto y
  una base temporal, con las keys vacías para que ningún proveedor quede configurado, y apagala al terminar:
  ```bash
  CL_PORT=8766 CL_DB_PATH=/tmp/cl-prueba.db ANTHROPIC_API_KEY= OPENAI_API_KEY= GEMINI_API_KEY= TYPESAFE_API_KEY= JEV_ENABLED=0 .venv/bin/python -m community_lens
  ```
  Las variables exportadas ganan sobre `.env` (`config.py`). `CL_DB_PATH` también mueve `staging/`, que la app
  vacía al arrancar: con la base real, arrancar otra instancia borra vistas previas de importación en curso.
- No hagás `git commit` ni `git push`: los hace el dueño. Trabajá en una rama, nunca sobre `main`
  (`fix/…`, `feature/…` o `chore/…`, con un nombre corto), dejá el cambio en el árbol de trabajo y pasale el
  comando con el mensaje propuesto: una línea corta con prefijo de tipo (`feature:`, `fix:`, `chore:`), sin
  líneas de co-autoría. Los cambios llegan a `main` por pull request, con las pruebas de CI en verde.
- No agregués dependencias (Python ni npm) sin preguntar: la app corre sin Node y sin internet, y cada
  dependencia es algo que el usuario instala en su máquina.
- No borrés ni renombrés columnas ni tablas. No hay migraciones: `db.py` crea lo que falta al conectar y existe
  una base real con el esquema actual. Un cambio de esquema o de datos derivados es aditivo, idempotente y se
  aplica al abrir una base vieja (el `ALTER TABLE` condicional en `db.connect` es el modelo). Una edición del
  periódico ya guardada tiene que seguir abriéndose: para cambiar su forma, seguí las instrucciones de la
  cabecera de `edition.py` (`FORMAT` y `upgrade()`).
- No modifiqués los archivos originales de los chats y no imprimás secretos ni texto de mensajes en logs:
  los logs registran conteos y estados, el log de accesos HTTP está apagado a propósito.

## Reglas del producto (decisiones, no sugerencias)

- Motor determinista: conteos y estadísticas oficiales los calcula SQL explícito (`stats.py`), nunca un modelo.
  Hechos, clasificaciones automáticas e interpretaciones de un modelo se muestran separados y etiquetados.
- Local primero: la app escucha solo en loopback. Nada sale de la máquina sin activación explícita del usuario,
  que tiene que ver qué texto se envía y a quién (la edición guarda eso en `editions.sent`).
- El contenido del chat es dato, nunca instrucciones para un modelo ni HTML. En el frontend el texto entra solo
  por `dom.ts` (sin `innerHTML`; `tests/test_api.py` falla si aparece) y la CSP no admite scripts en línea; los
  únicos estilos en línea son dos bloques fijos de la librería de gráficos, autorizados por hash en `api.py`.
- No inventar: el periódico solo enlaza a URLs que salieron del chat (el modelo se refiere a cada enlace por
  número y el código pone la dirección); no se unen autores por nombres parecidos; no se infieren intenciones.
- Detalle y porqués en [docs/seguridad.md](docs/seguridad.md), [docs/periodico.md](docs/periodico.md) y
  [docs/metricas.md](docs/metricas.md).

## Cómo verificar

```bash
.venv/bin/pip install -r requirements-dev.txt   # una vez
./scripts/check.sh                              # lo mismo que corre CI: linter, frontend y pruebas
```

Por separado: `.venv/bin/ruff check .` (linter; no se formatea el código, las líneas largas son a propósito),
`.venv/bin/python -m pytest -q` (sin red, base temporal por prueba, no lee tu `.env`) y `npm install && npm run check`
(solo si tocaste `web/src/`: regenera `api-schema.json` y `web/dist`). Un `# noqa` lleva al lado su razón.

`web/dist/` va versionado para que la app corra sin Node, y `web/src/api-schema.json` se genera desde
`types.ts`: si cambiás el frontend o una respuesta de la API y no corrés `npm run check`, `tests/test_contract.py`
falla (con `node_modules/` instalado; sin él esa prueba se salta, y en CI siempre corre). Entregá los archivos
regenerados junto con el cambio. Para la línea de comandos, `.venv/bin/python -m community_lens --help` y
`... periodico --help` solo imprimen ayuda. Qué cubre cada archivo de pruebas: [docs/arquitectura.md](docs/arquitectura.md).

## Dónde está cada cosa

| Qué | Dónde | Detalle |
|---|---|---|
| Arranque, configuración, esquema | `community_lens/__main__.py`, `config.py` (única lectura del entorno), `db.py` | [docs/arquitectura.md](docs/arquitectura.md) |
| Parser e importación con reconciliación | `parser.py`, `importer.py` | ídem |
| Estadísticas, búsqueda, recursos, reporte | `stats.py`, `search.py`, `resources.py`, `report.py` | [docs/metricas.md](docs/metricas.md) |
| Clasificación (reglas local, Jev opcional) | `classify/` | [docs/clasificacion.md](docs/clasificacion.md) |
| Periódico: reglas, modelos, plantilla, formato guardado, CLI | `newspaper.py`, `llm.py`, `newspaper_html.py`, `edition.py`, `cli.py` | [docs/periodico.md](docs/periodico.md) |
| API HTTP y cabeceras de seguridad | `api.py` | [docs/seguridad.md](docs/seguridad.md) |
| Frontend (TypeScript estricto, sin framework) | `web/src/` → `web/dist/`; librerías copiadas en `web/vendor/` | [docs/frontend.md](docs/frontend.md) |
| Diagramas de flujo de datos | `docs/diagramas/` | [docs/diagramas/README.md](docs/diagramas/README.md) |
| Variables de entorno | `.env.example` (todas, comentadas) | — |

## Cómo trabajar y entregar

- Todo en español: comentarios, docstrings, mensajes de la interfaz y de error, documentación y tus respuestas.
  Los identificadores del código siguen en inglés, como el resto. La interfaz usa voseo.
- Escribí código que se lea como el que lo rodea: SQL explícito sin ORM; cada módulo con su docstring de
  cabecera diciendo qué decide; frontend sin framework y con dependencias en un solo sentido
  (`app` → `router` → `views` → `components` → utilidades; una vista no importa a otra).
- La red está aislada en un punto por módulo y se reemplaza en las pruebas; si agregás algo que sale a la red,
  dale el mismo tratamiento y una prueba sin red.
- Reportá con lo importante primero y con exactitud qué verificaste: distinguí "lo reproduje" (corrí la prueba,
  el comando) de "lo leí en el código". No des por hecho lo que no corriste.
- No cambiés la documentación de `docs/` ni el README sin que el cambio de código lo exija; cuando lo exija,
  actualizalos en el mismo cambio.

Decisiones ya tomadas, para no volver a proponerlas: sin React ni otro framework en el frontend; gráficos con
Observable Plot + D3 copiados en `web/vendor/` (se evaluó otra librería y se descartó); diagramas con Archify;
proveedores de modelos configurados por `.env`, de los que solo Claude está probado contra el servicio real;
hay un refresh visual de la interfaz en evaluación, así que no reorganicés las vistas mientras tanto.
