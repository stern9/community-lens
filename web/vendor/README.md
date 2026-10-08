# Librerías incluidas (sin CDN)

Copias locales para que la app no cargue nada desde internet.

| Archivo | Proyecto | Versión | Licencia | Origen |
|---|---|---|---|---|
| `d3.min.js` | [D3](https://d3js.org) | 7.9.0 | ISC | `https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js` |
| `plot.umd.min.js` | [Observable Plot](https://observablehq.com/plot/) | 0.6.17 | ISC | `https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/dist/plot.umd.min.js` |

Cada archivo conserva su aviso de copyright en la primera línea.

Al actualizar Plot hay que revisar `PLOT_STYLE_HASHES` en `community_lens/api.py`: Plot inserta dos
bloques `<style>` fijos y la app los autoriza por hash. Si cambian, el navegador muestra en la consola
el hash nuevo que hay que poner.
