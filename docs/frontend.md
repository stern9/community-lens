# Frontend

TypeScript con la configuración más estricta (`strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
entre otras), sin framework ni empaquetador: `tsc` compila cada módulo de `web/src/` a `web/dist/`, y el navegador
los carga como módulos ES. `web/dist/` va versionado para que la app corra sin Node; Node (22 o superior) solo hace
falta para modificar el frontend:

```bash
npm install          # una vez
npm run check        # regenera el esquema de la API y compila web/src → web/dist
npm run typecheck    # solo verificar tipos
```

| Módulo | Contenido |
|---|---|
| `types.ts` | El contrato de la API: un tipo por cada respuesta y por cada cuerpo de solicitud |
| `api.ts` | Cliente: cada función fija la ruta junto con su tipo |
| `dom.ts` | Construcción del DOM; el contenido de los chats solo puede entrar como texto |
| `charts.ts` | Gráficos con Observable Plot |
| `format.ts` | Formato de números, fechas y etiquetas |
| `labels.ts` | Nombres legibles de los códigos de la API |
| `state.ts` | Estado compartido: grupo y período elegidos, configuración y lo guardado en memoria del grupo activo |
| `shell.ts` | El marco fijo de la página: zona de contenido, panel lateral, barra superior, avisos |
| `router.ts` | Traduce el `#hash` en una vista; una entrada por pestaña |
| `views/` | Una pantalla por archivo: las ocho pestañas (`resumen`, `explorar`, `recursos`, `stickers`, `categorias`, `reporte`, `periodico`, `importar`) y el panel de contexto de un mensaje (`contexto`) |
| `components/` | Piezas que usan varias pantallas (`message.ts`: la tarjeta de un mensaje) |
| `app.ts` | Punto de entrada: carga inicial y conexión de la barra superior con el enrutador |

Las dependencias van en un solo sentido: `app` → `router` → `views` → `components` → (`state`, `shell`, `api`, `dom`…).
Una vista no importa al enrutador ni a otra vista: recibe del enrutador sus parámetros y una función `refresh`
para volver a dibujarse. Agregar una pantalla es crear un archivo en `views/` y sumar una línea en `router.ts`.

**Estado.** Solo `state.ts` cambia qué grupo y período están elegidos (`selectChat`, `setPeriod`) y decide cuándo
lo guardado en memoria deja de valer (al cambiar de grupo, al importar o eliminar, al unir o separar alias). Las
vistas piden los datos con el alcance que necesitan: Resumen pide los autores del período; el filtro de Explorar
usa los autores del grupo en todo su historial (`chatAuthors`).

**Tipos.** Los tipos de `types.ts` no se dan por buenos: `npm run schema` genera `web/src/api-schema.json` a partir
de ellos, y `tests/test_contract.py` valida las respuestas reales de cada endpoint contra ese esquema, que no
admite campos de más ni de menos, y compara los cuerpos de solicitud con los modelos que valida el backend. Si
alguien cambia la API en Python sin actualizar `types.ts` (o al revés), esa prueba falla. La misma prueba verifica
que el TypeScript compile y que `web/dist/` y el esquema estén al día con las fuentes.

No se usó React: para estas pantallas, sin estado compartido complejo, no aportaba claridad y habría obligado a
sumar un empaquetador. Migrar más adelante no toca el backend ni `types.ts` / `api.ts`.

## Gráficos (Observable Plot)

Los gráficos se dibujan con [Observable Plot](https://observablehq.com/plot/) 0.6.17 sobre D3 7.9.0, ambos con
licencia ISC y copiados en `web/vendor/` para no depender de internet ni de Node.

| Gráfico | Qué muestra | Dato de origen |
|---|---|---|
| Mensajes por mes | Barras; los meses parciales van tenues y con asterisco | `/api/stats` → `monthly` |
| Calendario diario | Una celda por día, más oscuro = más mensajes | `/api/stats` → `daily` |
| Cuándo se conversa | Barras por día de la semana y por hora; como alternativa, mapa de calor día × hora | `/api/stats` → `weekday`, `hour`, `weekday_hour` |
| Categorías por mes (pestaña Categorías) | Un gráfico pequeño por categoría: % de los mensajes clasificados de cada mes | `/api/classify/monthly` |

Plot solo dibuja: todos los números salen del SQL del backend, y cada gráfico tiene la opción "Ver como tabla".
El calendario se puede enlazar directo con `#/resumen?vista=dia` y el mapa de calor con `#/resumen?ritmo=calor`.

**Por qué Plot y no ECharts.** Plot funcionó bajo la política de seguridad estricta de la app; lo único que
necesitó fue autorizar por hash los dos bloques `<style>` fijos que inserta (`PLOT_STYLE_HASHES` en `api.py`), sin
abrir `'unsafe-inline'`. Pesa la mitad que ECharts (unos 490 KB entre Plot y D3) y genera SVG, que respeta el
tema claro/oscuro. ECharts seguiría siendo la opción si hiciera falta zoom con arrastre o gráficos muy interactivos.

El reporte exportable no usa Plot: lleva su propio SVG para seguir siendo un único archivo HTML sin scripts.
