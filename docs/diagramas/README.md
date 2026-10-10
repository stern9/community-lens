# Diagramas de flujo de datos

| Diagrama | Archivos | En la app |
|---|---|---|
| Del archivo exportado al reporte (estadísticas y mensajes) | `flujo-de-datos.html`, fuente `flujo-de-datos.dataflow.json` | "Cómo funciona ↗" en la barra (grupo *Datos*) y "Ver el flujo de datos de la app ↗" en Resumen → `/como-funciona` |
| Cómo se arma una edición del periódico | `periodico.html`, `periodico.png` (para compartir), fuente `periodico.dataflow.json` | "Cómo se arma ↗", junto a "Generar periódico" → `/como-funciona/periodico` |

Sin la app, los `.html` se abren directo en el navegador.

Cada diagrama describe su propia parte. El primero cubre solo el modo de estadísticas: cuando dice que Jev es
"lo único que sale de la máquina" se refiere a ese modo. Lo que envía el periódico está en el segundo.

Están hechos con [Archify](https://github.com/tt-a1i/archify) (v3.0.1, commit `73aaa06`, licencia MIT), una
herramienta que convierte una descripción en JSON en un diagrama HTML autocontenido con zoom, trazado de rutas,
tema claro/oscuro y exportación. De sus cinco tipos (`architecture`, `workflow`, `sequence`, `dataflow`,
`lifecycle`) se usa **`dataflow`**, porque lo que más importa explicar es qué pasa con los datos en cada etapa:
cuál es el camino principal, qué es lectura local, qué es una estimación y qué sale de la máquina.

Archify dibuja diagramas de sistemas, no gráficos de datos (de esos se encarga Observable Plot; ver
[../frontend.md](../frontend.md)). Tampoco es una dependencia de ejecución: el HTML generado está versionado y la
app solo lo sirve.

## Regenerarlos

Después de editar un `.dataflow.json` (requiere Node):

```bash
git clone --depth 1 https://github.com/tt-a1i/archify.git /tmp/archify
node /tmp/archify/archify/bin/archify.mjs validate dataflow docs/diagramas/flujo-de-datos.dataflow.json
node /tmp/archify/archify/bin/archify.mjs render   dataflow docs/diagramas/flujo-de-datos.dataflow.json docs/diagramas/flujo-de-datos.html
```

Lo mismo con `periodico.dataflow.json` → `periodico.html`.

Los diagramas no contienen datos de ningún chat. Como llevan scripts en línea, la app los sirve con una política
propia que les impide hacer conexiones de red o llamar a la API (`connect-src 'none'`); el resto de la app
conserva su política estricta, sin scripts en línea.
