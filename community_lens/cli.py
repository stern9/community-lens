"""Periódico sin pantalla, para correr programado (cron, launchd, un agente, una acción de CI).

  python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --dias 7 --salida sitio/
  python -m community_lens periodico --carpeta exportaciones/ --grupo-id 3 --continuar --borrador previa/borrador.html --json
  python -m community_lens periodico --grupo-id 3 --edicion 12 --aprobar --aprobado-por "Jaime" --canal WhatsApp --salida sitio/

Dos modos que no se mezclan. Generar (lo de siempre): importa el chat (si se pasa --archivo, o la exportación más
nueva de --carpeta), genera la edición del rango y, con --salida, escribe un sitio estático. La edición nueva queda
como borrador y no entra al sitio hasta que alguien la apruebe; --borrador escribe su vista previa: la versión
pública (la que se publicaría, sin lecturas de la conversación ni el nombre del grupo) con la banda "Borrador, no
compartir", que es lo que aprueba quien revisa. Actuar (--edicion N con --aprobar, --descartar, --recuperar o --borrador, o --listar):
trabaja sobre lo guardado sin importar, generar, llamar al modelo ni descargar páginas. El sitio solo lleva
ediciones aprobadas (los bytes guardados al aprobarlas) y las heredadas de antes de que existiera la aprobación.
--aprobar al generar aprueba la recién generada sin que nadie vea el borrador: es para uso manual, nunca para un
guion programado. Con --continuar, un borrador pendiente frena la corrida (código 3) hasta aprobarlo o descartarlo.
Enviar texto del chat a un modelo exige pedir --lecturas y además --confirmo-enviar-chat.

Códigos de salida, para que un guion decida sin leer mensajes: 0 hecho (edición generada, acción aplicada, sitio
publicado con --solo-publicar o listado); 1 algo falló y hay que mirar (también si una edición no se pudo escribir
en el sitio: una aprobada cuyo HTML no coincide con su hash, aprobada antes de la versión pública o con bytes con
lecturas de la conversación, o una guardada que esta versión no sabe leer); 2 argumentos mal usados (los rechaza argparse); 3 nada que
publicar esta vez (sin novedad, material insuficiente o un borrador pendiente), que se avisa y no reescribe el
sitio. Con --json la salida estándar lleva un solo objeto JSON con el resultado y los mensajes para personas van a
la salida de error; nunca lleva texto de mensajes del chat. La excepción es el código 2: argparse rechaza los
argumentos antes de que exista resultado, así que ahí no hay JSON, solo el mensaje de uso en stderr.

El rango, el máximo de artículos y a qué grupo se refiere un nombre los resuelve newspaper.py, y los estados de una
edición edition.py, con las mismas reglas que usa la pantalla.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import edition, importer, llm, newspaper
from .config import settings
from .db import connect

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOTHING = 0, 1, 2, 3
EXPORTS = (".zip", ".txt")
EPILOG = """modos:
  generar  lo de siempre: importa (--archivo, --carpeta), genera la edición del rango y la deja como borrador;
           --salida escribe el sitio solo con las aprobadas y heredadas, --borrador la vista previa: la versión
           pública con banda, la que se publicaría y aprueba quien revisa
  actuar   --edicion N con --aprobar, --descartar, --recuperar o --borrador, o --listar: no importa, no genera,
           no llama al modelo ni descarga páginas; --edicion no se combina con --archivo, --carpeta, --continuar,
           --dias, --desde ni --hasta

  --aprobar al generar aprueba la edición sin que nadie haya visto el borrador: solo para uso manual, NUNCA en un
  guion programado. Un guion genera el borrador; una persona lo revisa y lo aprueba con --edicion N --aprobar.

códigos de salida:
  0  hecho: edición generada, acción aplicada (aprobada, descartada, recuperada, vista previa), sitio publicado
     con --solo-publicar o listado
  1  error: fecha inválida, grupo ambiguo, sin modelo, falló el modelo, archivo o carpeta sin exportaciones,
     edición de otro grupo, o una edición que no se escribió en el sitio: dañada, aprobada antes de la versión
     pública o con bytes con lecturas (el detalle dice por qué)
  2  argumentos mal usados (incluye --carpeta sin --grupo-id ni --grupo, --aprobar sin --aprobado-por o --canal,
     o alguno de ellos vacío o demasiado largo)
  3  nada que publicar esta vez: sin novedad, material insuficiente o (con --continuar) un borrador pendiente;
     no reescribe el sitio"""


def _newest_export(folder: Path) -> Path:
    """La exportación (.zip o .txt) más nueva por fecha de modificación, sin ocultos ni subcarpetas. No toca nada."""
    if not folder.is_dir():
        raise newspaper.NewspaperError(f"No existe la carpeta {folder}.")
    found = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in EXPORTS]
    if not found:
        raise newspaper.NewspaperError(f"La carpeta {folder} no tiene ninguna exportación .zip o .txt.")
    return max(found, key=lambda p: (p.stat().st_mtime, p.name))  # a igual fecha, el último por nombre: determinista


def _import(con, path: Path, chat, group: str | None, tz: str, date_order: str | None, say=print) -> dict:
    """Importa el archivo en `chat` (el grupo ya resuelto por nombre o id) o, si no hay, donde corresponda.
    Devuelve qué pasó: archivo, estado ("imported" o "duplicate"), grupo y conteos de la reconciliación."""
    if not path.is_file():
        raise importer.ImportError_("no_file", f"No existe el archivo {path}.")
    data, name = importer.unzip_chat(path.read_bytes(), path.name)
    pv = importer.preview(con, data, name, tz, date_order)
    if pv["needs_date_order"]:
        raise importer.ImportError_("date_order_required", "No se pudo detectar el orden de fecha del archivo; indicalo con --orden-fecha MDY, DMY o YMD.")
    rec = pv["recommendation"]
    if chat is None and not group:
        # Sin --grupo: el grupo que lleva el nombre del archivo; si no existe, el que el contenido reconoce; si no, se crea.
        chat = newspaper.find_chat(con, pv["suggested_name"])
    chat_id = chat["id"] if chat else (rec["chat_id"] if rec and not group else None)
    res = importer.commit(con, pv["token"], name, chat_id=chat_id, chat_name=(group or pv["suggested_name"]).strip(), tz_name=tz,
                          date_order=pv["date_order"])
    r = res.get("reconciliation") or {"added": 0, "matched_existing": 0}
    if res["status"] == "duplicate":
        say(f"El archivo ya estaba importado (grupo {res['chat_id']}).")
    else:
        say(f"Importado en el grupo {res['chat_id']}: {r['added']} registros nuevos, {r['matched_existing']} ya estaban.")
    return {"file": path.name, "status": res["status"], "chat_id": res["chat_id"], "added": r["added"], "matched_existing": r["matched_existing"]}


def _state(e: dict) -> str:
    """El estado de una edición en una línea, para personas: el mismo que muestra la lista de la pantalla."""
    ap = e["approval"]
    line = e["status"] + (f" por {ap['by']} ({ap['via']}, {ap['at']})" if ap and ap["by"] and e["status"] == "aprobada" else "")
    line += " · en el sitio" if e["in_site"] else " · fuera del sitio"
    if e["blocked_reason"]:
        line += f" · {e['blocked_reason']}"
    elif e["up_to_date"] is False:
        line += " · hay plantilla nueva: reaprobala para actualizarla"
    return line


def _list(con, chat, out: dict, say) -> None:
    """--listar: las ediciones del grupo, la más reciente primero."""
    out["editions"] = rows = edition.listing(con, chat["id"])
    for e in rows:
        say(f"{e['id']}  {e['from']} a {e['to']}  {_state(e)}")
    if not rows:
        say(f"El grupo {chat['id']} todavía no tiene ediciones.")


def _act(con, chat, a, approve_to: str | None, say) -> str:
    """--edicion N: aprueba, descarta o recupera esa edición (las tres no se combinan; puede no haber ninguna si solo
    se pide --borrador). La edición tiene que ser del grupo indicado. Devuelve el status del resultado."""
    if not any(e["id"] == a.edicion for e in edition.listing(con, chat["id"])):
        raise newspaper.NewspaperError(f"La edición {a.edicion} no existe o no es del grupo {chat['id']}; --listar muestra las suyas.")
    if a.aprobar:
        edition.approve(con, a.edicion, a.aprobado_por, a.canal, approve_to)
        say(f"Edición {a.edicion} aprobada: " + (f"se escribe ahora en {a.salida}." if a.salida else
                                                    "entra al sitio la próxima vez que se escriba con --salida."))
        return "aprobada"
    if a.descartar:
        edition.discard(con, a.edicion)
        say(f"Edición {a.edicion} descartada: " + (f"sale ahora de {a.salida}." if a.salida else
                                                      "sale del sitio la próxima vez que se escriba con --salida."))
        return "descartada"
    if a.recuperar:
        edition.restore(con, a.edicion)
        say(f"Edición {a.edicion} recuperada como borrador: hay que aprobarla para que entre al sitio.")
        return "recuperada"
    return "vista_previa"


def periodico(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m community_lens periodico", description="Genera una edición del periódico, la aprueba y la publica como sitio estático.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--archivo", type=Path, help="exportación de WhatsApp (.zip o .txt) a importar antes de generar")
    src.add_argument("--carpeta", type=Path, help="carpeta de exportaciones: importa la .zip o .txt más nueva; exige --grupo-id o --grupo")
    ap.add_argument("--grupo", help="nombre exacto del grupo en Community Lens (con --archivo, se crea si no existe)")
    ap.add_argument("--grupo-id", type=int, help="id del grupo, en lugar de --grupo; hace falta si dos grupos se llaman igual")
    ap.add_argument("--orden-fecha", choices=["MDY", "DMY", "YMD"], help="orden de fecha del archivo, si no se puede detectar")
    ap.add_argument("--dias", type=int,
                    help=f"días que cubre la edición, contando hacia atrás desde el último día con mensajes ({newspaper.DEFAULT_DAYS})")
    ap.add_argument("--desde", help="AAAA-MM-DD; reemplaza a --dias")
    ap.add_argument("--hasta", help="AAAA-MM-DD (por defecto, el último día con mensajes)")
    ap.add_argument("--continuar", action="store_true",
                    help="cubrir desde el día siguiente a la última edición aprobada hasta el último día completo; sin novedad si no hay "
                         "días nuevos, y código 3 si queda un borrador sin aprobar ni descartar")
    ap.add_argument("--articulos", type=int, default=newspaper.LIMITS["article"][0],
                    help=f"máximo de artículos, de {newspaper.MIN_ARTICLES} a {newspaper.LIMITS['article'][1]} ({newspaper.LIMITS['article'][0]})")
    ap.add_argument("--orden", choices=["recent", "shared"], default="recent")
    ap.add_argument("--modelo", help="proveedor que redacta: anthropic, openai o gemini (por defecto, el primero configurado que responda)")
    ap.add_argument("--lecturas", default="", help="proveedores que dan su lectura de la conversación, separados por coma, o «todos» los configurados")
    ap.add_argument("--confirmo-enviar-chat", action="store_true", help="obligatorio con --lecturas: envía mensajes seudonimizados al modelo")
    ap.add_argument("--salida", type=Path, help="carpeta donde escribir el sitio estático (index.html + ediciones/), solo con ediciones aprobadas")
    ap.add_argument("--solo-publicar", action="store_true", help="no genera: solo reescribe el sitio con las ediciones aprobadas")
    ap.add_argument("--borrador", type=Path, metavar="RUTA.html",
                    help="escribe la vista previa (de la recién generada o de --edicion): la versión pública, sin lecturas ni el nombre del "
                         "grupo, con la banda «Borrador, no compartir»; no toca el sitio")
    ap.add_argument("--edicion", type=int, metavar="N", help="actuar sobre la edición N del grupo en lugar de generar una")
    ap.add_argument("--aprobar", action="store_true",
                    help="aprueba --edicion N o, al generar, la recién generada: esto último solo a mano, NUNCA en un guion programado "
                         "(aprobaría sin que nadie vea el borrador); exige --aprobado-por y --canal")
    ap.add_argument("--aprobado-por", metavar="TEXTO", help="con --aprobar: quién aprobó")
    ap.add_argument("--canal", metavar="TEXTO", help="con --aprobar: por qué canal se aprobó (WhatsApp, correo…)")
    ap.add_argument("--destino", metavar="TEXTO", help="con --aprobar: adónde se publica (por defecto, la ruta de --salida; sin ella, no se registra)")
    ap.add_argument("--descartar", action="store_true", help="con --edicion: la oculta y la saca del sitio; se puede recuperar")
    ap.add_argument("--recuperar", action="store_true", help="con --edicion: una descartada vuelve a borrador")
    ap.add_argument("--listar", action="store_true", help="imprime las ediciones del grupo con su estado, quién aprobó y si están en el sitio")
    ap.add_argument("--json", action="store_true", help="imprime el resultado como un objeto JSON en la salida estándar; los mensajes van a la de error")
    a = ap.parse_args(argv)
    # Todos estos salen con código 2, como todo argumento mal usado.
    generating = [o for o, v in (("--archivo", a.archivo), ("--carpeta", a.carpeta), ("--continuar", a.continuar), ("--dias", a.dias is not None),
                                 ("--desde", a.desde), ("--hasta", a.hasta)) if v]
    if a.continuar and (a.dias is not None or a.desde or a.hasta):
        ap.error("--continuar calcula el rango solo: no se combina con --dias, --desde ni --hasta.")
    if a.solo_publicar and (a.continuar or not a.salida):
        ap.error("--solo-publicar reescribe el sitio con lo guardado: necesita --salida y no se combina con --continuar.")
    if a.carpeta and not a.grupo and a.grupo_id is None:
        ap.error("--carpeta exige --grupo-id o --grupo: sin eso el grupo sale del nombre del archivo y, si la exportación "
                 "cambia de nombre, se crearía otro grupo.")
    if a.listar and (generating or a.edicion is not None or a.solo_publicar or a.salida or a.borrador or a.aprobar or a.descartar or a.recuperar):
        ap.error("--listar solo muestra las ediciones del grupo: no se combina con otras acciones, con --salida ni con la generación.")
    if a.edicion is not None and generating:
        ap.error(f"--edicion actúa sobre una edición guardada sin generar: no se combina con {', '.join(generating)}.")
    if a.edicion is not None and a.solo_publicar:
        ap.error("--edicion ya escribe el sitio si se pasa --salida: no hace falta --solo-publicar.")
    if a.aprobar + a.descartar + a.recuperar > 1:
        ap.error("--aprobar, --descartar y --recuperar no se combinan: elegí una.")
    if (a.descartar or a.recuperar) and a.edicion is None:
        ap.error("--descartar y --recuperar necesitan --edicion N.")
    if a.edicion is not None and not (a.aprobar or a.descartar or a.recuperar or a.borrador):
        ap.error("--edicion necesita una acción: --aprobar, --descartar, --recuperar o --borrador.")
    if a.aprobar and not (a.aprobado_por and a.canal):
        ap.error("--aprobar exige --aprobado-por y --canal: quién aprobó y por qué canal quedan registrados.")
    if not a.aprobar and (a.aprobado_por or a.canal or a.destino):
        ap.error("--aprobado-por, --canal y --destino solo van con --aprobar.")
    if (a.aprobar or a.borrador) and a.solo_publicar and a.edicion is None:
        ap.error("--aprobar y --borrador actúan sobre la edición recién generada o sobre --edicion N: --solo-publicar no genera ninguna.")
    approve_to = None
    if a.aprobar:  # los mismos límites que approve(), antes de abrir la base, importar o llamar al modelo
        try:
            a.aprobado_por, a.canal, approve_to = edition.check_approval(a.aprobado_por, a.canal, a.destino or (str(a.salida) if a.salida else None))
        except ValueError as err:
            ap.error(f"--aprobado-por, --canal o --destino: {err}")
    acting = a.edicion is not None or a.listar

    def say(*msg) -> None:
        print(*msg, file=sys.stderr if a.json else sys.stdout)

    out = {"status": "error", "detail": None, "import": None, "edition": None, "warnings": [], "site": None, "draft": None}
    code, con = EXIT_ERROR, None

    def failed(err) -> None:
        # La edición se guarda antes de escribir el sitio: si falló después, ya está en la base. Hay que decir cómo
        # seguir, o queda como borrador para siempre (y frena el próximo --continuar).
        detail = str(getattr(err, "message", None) or err)
        e = out["edition"]
        if e and a.aprobar and e["status"] == "aprobada":
            detail += f" La edición {e['id']} quedó aprobada: para escribir el sitio corré --solo-publicar --salida …"
        elif e and not acting:
            detail += (f" La edición {e['id']} quedó guardada como borrador: para aprobarla y publicarla corré --edicion {e['id']} "
                       "--aprobar --aprobado-por … --canal … --salida …")
        out["detail"] = detail
        print("No se pudo:", detail, file=sys.stderr)

    try:
        con = connect()
        chat = newspaper.find_chat(con, a.grupo, a.grupo_id) if a.grupo or a.grupo_id is not None else None
        if a.carpeta:
            a.archivo = _newest_export(a.carpeta)
            say(f"Exportación más nueva de la carpeta: {a.archivo.name}")
        if a.archivo:
            out["import"] = _import(con, a.archivo, chat, a.grupo, settings().default_tz, a.orden_fecha, say)
            chat = newspaper.find_chat(con, chat_id=out["import"]["chat_id"])
        elif chat is None:
            raise newspaper.NewspaperError("Indicá el grupo con --grupo (nombre exacto) o --grupo-id." if acting else
                                           "Indicá --archivo para importar, o un grupo ya importado con --grupo (nombre exacto) o --grupo-id.")

        status, eid = "publicada", a.edicion
        if a.listar:
            _list(con, chat, out, say)
            status = "listado"
        elif acting:
            status = _act(con, chat, a, approve_to, say)
            out["edition"] = edition.listed(con, eid)
        elif not a.solo_publicar:
            d_from, d_to = (newspaper.continue_range(con, chat["id"]) if a.continuar else
                            newspaper.resolve_range(con, chat["id"], a.desde, a.hasta, newspaper.DEFAULT_DAYS if a.dias is None else a.dias))
            if a.continuar and (p := newspaper.pending_draft(con, chat["id"])):
                more = f" (hay {p['count']} borradores; este es el más nuevo)" if p["count"] > 1 else ""
                raise newspaper.DraftPending(
                    f"La edición {p['id']} ({p['from']} a {p['to']}) sigue como borrador{more}: no se genera otra encima. Aprobala con "
                    f"--edicion {p['id']} --aprobar --aprobado-por … --canal …, o descartala con --edicion {p['id']} --descartar.")
            if not llm.providers():
                raise llm.LLMError("No hay ningún modelo configurado en .env (ver .env.example).")
            readers = [newspaper.ALL if x.strip() == "todos" else x.strip() for x in a.lecturas.split(",") if x.strip()]
            res = newspaper.generate(con, chat["id"], chat["name"], d_from, d_to, editor=a.modelo or newspaper.AUTO, readers=readers,
                                     consent_chat=a.confirmo_enviar_chat, order=a.orden, max_articles=a.articulos,
                                     min_articles=newspaper.MIN_ARTICLES if a.continuar else 1)
            s, eid, counts = res["sent"], res["id"], {k: res[k] for k in ("articles", "socials", "tools", "unread", "others", "readings")}
            out["edition"] = {**edition.listed(con, eid), **counts}
            out["warnings"] = res["warnings"]
            say(f"Edición {eid} ({d_from} a {d_to}): {res['articles']} artículos, {res['socials']} redes, {res['tools']} sitios, "
                f"{res['unread']} sin leer, {res['others']} en otros enlaces, {res['readings']} lecturas.")
            say(f"Enviado: {s['pages']} páginas a {', '.join([*s['pages_tried'], s['pages_to']])}; " +
                (f"{s['chat_messages']} mensajes seudonimizados a {', '.join(s['chat_to'])}." if s["chat_messages"] else "nada del chat."))
            for w in res["warnings"]:
                say("Aviso:", w)
            if a.aprobar:
                edition.approve(con, eid, a.aprobado_por, a.canal, approve_to)
                out["edition"] = {**edition.listed(con, eid), **counts}
                say(f"Edición {eid} aprobada al generarla.")
            else:
                say(f"La edición {eid} quedó como borrador: no entra al sitio hasta aprobarla, aunque se pase --salida "
                    f"(--edicion {eid} --aprobar --aprobado-por … --canal …).")
            status = "generada"
        if a.borrador:
            a.borrador.parent.mkdir(parents=True, exist_ok=True)
            a.borrador.write_text(newspaper.edition_html(con, eid, draft=True, public=True)[0], encoding="utf-8")
            out["draft"] = str(a.borrador)
            say(f"Vista previa de la edición {eid} (versión pública, con la banda de borrador) escrita en {a.borrador}.")
        if a.salida:
            site = newspaper.publish(con, chat["id"], a.salida)
            files = site["files"]
            out["site"] = {"dir": str(a.salida), "files": len(files), "skipped": site["skipped"]}
            say(f"Sitio escrito en {a.salida}: {len(files) - 1} edición(es) + index.html")
            for sk in site["skipped"]:
                print(f"Aviso: la edición {sk['id']} ({sk['from']} a {sk['to']}) no se escribió: {sk['reason']}", file=sys.stderr)
        code, out["status"] = EXIT_OK, status
        if out["site"] and out["site"]["skipped"]:
            # El sitio se escribió con las demás, pero hay que mirar las que faltan.
            n = len(out["site"]["skipped"])
            code, out["status"] = EXIT_ERROR, "error"
            out["detail"] = f"{n} edición(es) no se escribieron en el sitio: " + " ".join(
                f"la {sk['id']} ({sk['from']} a {sk['to']}): {sk['reason']}" for sk in out["site"]["skipped"])
    except (newspaper.NoNews, newspaper.NoMaterial, newspaper.DraftPending) as err:
        # No es una falla: esta vez no hay nada nuevo que generar. El sitio no se reescribe (para eso está --solo-publicar).
        code, out["detail"] = EXIT_NOTHING, str(err)
        out["status"] = ("sin_novedad" if isinstance(err, newspaper.NoNews) else "borrador_pendiente" if isinstance(err, newspaper.DraftPending)
                         else "material_insuficiente")
        say("Aviso:", err)
    except (newspaper.NewspaperError, llm.LLMError, importer.ImportError_, ValueError) as err:
        failed(err)
    except (OSError, sqlite3.Error) as err:  # carpeta sin permisos, --salida que es un archivo, base bloqueada…
        failed(err)
    finally:
        if con is not None:
            con.close()
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    return code
