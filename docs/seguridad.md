# Privacidad y seguridad

Qué sale de la máquina, y cuándo, está en la tabla "Privacidad" del [README](../README.md). Aquí va el resto.

## Red

- La app escucha solo en `127.0.0.1`. No hay autenticación: no cambiés `CL_HOST` en una red compartida (si lo
  hacés, la app lo avisa al arrancar y en la barra superior).
- **Solo responde a su propia dirección.** Que escuche en `127.0.0.1` no alcanza: tu navegador también está en
  esta máquina y corre páginas de otros. Una de esas páginas puede hacer que su propio nombre pase a apuntar a
  `127.0.0.1` (se conoce como *DNS rebinding*) y, como para el navegador sigue siendo "el mismo sitio", leer la API
  entera: búsquedas y mensajes incluidos. Por eso la app mira a qué dirección iba dirigida cada solicitud (el
  encabezado `Host`) y solo atiende `localhost`, `127.0.0.1` o `[::1]` con el puerto configurado. Cualquier otra
  recibe un error 421, sea la API, una página o un archivo. La comparación es exacta: no valen un nombre que
  contenga uno permitido, otro puerto ni un punto final, y no se miran encabezados de proxy (`X-Forwarded-Host`).
- **Solo modifica datos si la solicitud viene de la propia app.** Una página ajena también puede enviarle una
  solicitud a la dirección correcta sin poder leer la respuesta, y con eso bastaría para deshacer una unión de
  alias o cambiar la clasificación vigente. El navegador anota de qué sitio sale cada solicitud (`Origin`,
  `Sec-Fetch-Site`) y una página no puede cambiarlo: todo lo que no sea leer (`POST`, `PATCH`, `DELETE`…) se
  rechaza con un error 403 si esa anotación no dice exactamente "esta misma app". Una solicitud sin esa anotación
  (`curl`, un script tuyo) se atiende: los navegadores actuales la ponen siempre, así que no viene de una página.
- **Qué no cubre.** Sigue sin haber autenticación: cualquier programa de esta máquina puede usar la API, y con
  `CL_HOST` fuera de loopback también cualquier equipo de la red. En ese caso la app acepta además que la abrás por
  la dirección IP de la máquina (una dirección IP no se puede hacer pasar por otra con el truco del nombre); para
  abrirla con un nombre hay que listarlo en `CL_ALLOWED_HOSTS` (o usarlo como `CL_HOST`), y el nombre solo vale
  con el puerto de la app. Un proxy delante no está contemplado: si reenvía el nombre público, la app responde 421
  a todo; y aunque reenvíe `Host: 127.0.0.1:8765`, detrás de HTTPS las modificaciones se rechazan, porque la app
  solo reconoce como propio el origen `http://` por el que la sirve.
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
- Cada edición guarda qué se envió y a quién (`editions.sent`) y, cuando se aprueba, quién la aprobó, cuándo, por
  qué canal y adónde va (`editions.approval`, texto que escribe quien aprueba; no se registra en los logs). Al
  aprobarla se guardan también los bytes exactos de la página que se publica (`editions.approved_html`) y su sha256.
  Esos bytes son la versión pública: sin las lecturas de la conversación, sin el nombre del grupo y sin las cifras
  del chat; la pantalla de la app sigue mostrando la edición completa. Publicar escribe esos bytes tal cual y no los
  escribe, avisándolo, si ya no coinciden con el hash, si se aprobaron antes de que existiera la versión pública o si
  llevan lecturas de la conversación: en los dos últimos casos hay que aprobarla de nuevo.

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
