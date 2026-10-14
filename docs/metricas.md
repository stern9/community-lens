# Definiciones de métricas

La UI y la API (`/api/stats` → `definitions`) muestran estas definiciones junto a cada cifra.
Todas las calcula SQL sobre la base local: el mismo archivo da siempre el mismo resultado.

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
no importancia. El dato también está en `/api/stats` (`concentration.curve`, `half_rank`).

No se calcula "miembros silenciosos": el TXT no trae la lista de miembros.

## Categorías

Son estimaciones (con las correcciones humanas aplicadas), no conteos oficiales.

- **Mensaje clasificado**: el que la ejecución vigente cubrió, o el que tiene una corrección humana vigente aunque
  la ejecución no lo haya cubierto. Es la misma cuenta en el resumen y en el detalle por mes: el total del período
  es la suma de los meses.
- **Categorías por mes** usa como denominador los mensajes *clasificados* de ese mes, no todos los mensajes: una
  ejecución de Jev cubre como máximo `JEV_MAX_MESSAGES`, y la UI avisa cuando un mes tiene menos de la mitad
  clasificada.
- Un mensaje puede tener varias categorías, así que los conteos por categoría no suman el total.

## Enlaces

Las "compartidas" de un enlace son los mensajes distintos que lo incluyen. Qué cuenta como "el mismo enlace" (la
misma regla en Recursos y en el periódico) está en [periodico.md](periodico.md#quién-produce-cada-parte).
