# Privacidad y seguridad

Qué sale de la máquina, y cuándo, está en la tabla "Privacidad" del [README](../README.md). Aquí va el resto.

## Red

- La app escucha solo en `127.0.0.1`. No hay autenticación: no cambiés `CL_HOST` en una red compartida (si lo
  hacés, la app lo avisa al arrancar y en la barra superior).
- **Páginas enlazadas.** Un enlace del chat lo escribió cualquier miembro del grupo, así que al descargarlo:
  solo se aceptan direcciones `http(s)` públicas; se rechaza el nombre entero si alguna de sus direcciones es
  privada o local; la conexión se hace a la misma dirección IP que se verificó (no se vuelve a resolver el
  nombre); y las redirecciones se siguen una por una, verificando cada destino **antes** de pedirlo. Una
  redirección hacia `127.0.0.1` o `192.168.x.x` no llega a emitir la solicitud. Se leen como máximo 3 MB por página.
  La descarga no usa los proxies del entorno (`http_proxy`, `https_proxy`): se conecta directo.
- El frontend no carga nada de internet: las librerías de gráficos están copiadas en `web/vendor/`.

## Qué se guarda y qué se borra

- Todo queda en la base SQLite local (`data/`, fuera del repositorio). `.gitignore` excluye `Chats/`, `data/`,
  bases SQLite, `reports/` y `.env`.
- **Eliminar grupo y datos** (pestaña *Importar chat*) borra mensajes, índice de búsqueda, enlaces,
  clasificaciones, correcciones, las ediciones del periódico de ese grupo y el texto de las páginas que se leyeron
  para ellas (salvo las que otro grupo también enlaza), y compacta la base. Tus archivos originales no se tocan.
- La vista previa de importación guarda una copia temporal en `data/staging/`; se borra al confirmar, al
  arrancar la app y pasada una hora.
- Cada edición guarda qué se envió y a quién.

## Secretos y registros

- Las keys se leen de `.env`, no se registran, no aparecen en los mensajes de error y nunca llegan al navegador.
- Los logs registran conteos y estados, nunca texto de mensajes ni secretos; el log de accesos HTTP está
  desactivado porque las URLs de búsqueda contienen términos del chat.

## El contenido del chat es dato

- El frontend solo lo inserta como texto (sin `innerHTML`; el tipo `Child` de `dom.ts` no admite HTML) y la app
  envía una Content-Security-Policy sin scripts en línea; los únicos estilos en línea permitidos son dos bloques
  fijos de la librería de gráficos, autorizados por hash.
- El texto de las páginas y de los mensajes se le pasa a los modelos como material a resumir, con la indicación de
  tratar cualquier instrucción que contenga como contenido. Lo que devuelve el modelo se valida y entra escapado en
  la plantilla; las direcciones las pone el código.
- El periódico y el reporte son HTML sin scripts; los diagramas (que sí llevan scripts en línea) se sirven con una
  política propia que les impide hacer conexiones de red o llamar a la API.
- En las lecturas de la conversación los mensajes van con seudónimos (P01, P02…) y sin teléfonos, correos ni
  menciones. Un nombre de pila escrito dentro de un mensaje sí puede ir. La pantalla muestra cuántos mensajes se
  enviarían antes de generar.
