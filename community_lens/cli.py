"""Periódico sin pantalla, para correr programado (cron, launchd, un agente, una acción de CI).

  python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --dias 7 --salida sitio/
  python -m community_lens periodico --carpeta exportaciones/ --grupo-id 3 --continuar --salida sitio/ --json

Importa el chat (si se pasa --archivo, o la exportación más nueva de --carpeta), genera la edición del rango y
escribe un sitio estático en --salida. Enviar texto del chat a un modelo exige pedir --lecturas y además
--confirmo-enviar-chat.

Códigos de salida, para que un guion decida sin leer mensajes: 0 edición generada (o sitio publicado con
--solo-publicar); 1 algo falló y hay que mirar; 2 argumentos mal usados (los rechaza argparse, también --carpeta
sin --grupo-id ni --grupo); 3 nada que publicar esta vez (sin novedad, o material insuficiente), que se avisa y
no reescribe el sitio. Con --json la salida estándar lleva un solo objeto JSON con el resultado y los mensajes
para personas van a la salida de error; nunca lleva texto de mensajes del chat. La excepción es el código 2: argparse
rechaza los argumentos antes de que exista resultado, así que ahí no hay JSON, solo el mensaje de uso en stderr.

El rango, el máximo de artículos y a qué grupo se refiere un nombre los resuelve newspaper.py, con las mismas
reglas que usa la pantalla.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import importer, llm, newspaper
from .config import settings
from .db import connect

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOTHING = 0, 1, 2, 3
EXPORTS = (".zip", ".txt")
EPILOG = """códigos de salida:
  0  edición generada, o sitio publicado con --solo-publicar
  1  error: fecha inválida, grupo ambiguo, sin modelo, falló el modelo, archivo o carpeta sin exportaciones…
  2  argumentos mal usados (incluye --carpeta sin --grupo-id ni --grupo)
  3  nada que publicar esta vez: sin novedad o material insuficiente; no reescribe el sitio"""


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


def periodico(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m community_lens periodico", description="Genera una edición del periódico y la publica como sitio estático.",
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
                    help="cubrir desde el día siguiente a la última edición hasta el último día completo; sin novedad si no hay días nuevos")
    ap.add_argument("--articulos", type=int, default=newspaper.LIMITS["article"][0],
                    help=f"máximo de artículos, de {newspaper.MIN_ARTICLES} a {newspaper.LIMITS['article'][1]} ({newspaper.LIMITS['article'][0]})")
    ap.add_argument("--orden", choices=["recent", "shared"], default="recent")
    ap.add_argument("--modelo", help="proveedor que redacta: anthropic, openai o gemini (por defecto, el primero configurado que responda)")
    ap.add_argument("--lecturas", default="", help="proveedores que dan su lectura de la conversación, separados por coma, o «todos» los configurados")
    ap.add_argument("--confirmo-enviar-chat", action="store_true", help="obligatorio con --lecturas: envía mensajes seudonimizados al modelo")
    ap.add_argument("--salida", type=Path, help="carpeta donde escribir el sitio estático (index.html + ediciones/)")
    ap.add_argument("--solo-publicar", action="store_true", help="no genera: solo reescribe el sitio con las ediciones guardadas")
    ap.add_argument("--json", action="store_true", help="imprime el resultado como un objeto JSON en la salida estándar; los mensajes van a la de error")
    a = ap.parse_args(argv)
    if a.continuar and (a.dias is not None or a.desde or a.hasta):
        ap.error("--continuar calcula el rango solo: no se combina con --dias, --desde ni --hasta.")  # sale con código 2, como todo argumento mal usado
    if a.solo_publicar and (a.continuar or not a.salida):
        ap.error("--solo-publicar reescribe el sitio con lo guardado: necesita --salida y no se combina con --continuar.")
    if a.carpeta and not a.grupo and a.grupo_id is None:
        ap.error("--carpeta exige --grupo-id o --grupo: sin eso el grupo sale del nombre del archivo y, si la exportación "
                 "cambia de nombre, se crearía otro grupo.")

    def say(*msg) -> None:
        print(*msg, file=sys.stderr if a.json else sys.stdout)

    out = {"status": "error", "detail": None, "import": None, "edition": None, "warnings": [], "site": None}
    code, con = EXIT_ERROR, None

    def failed(err) -> None:
        # La edición se guarda antes de escribir el sitio: si falló después, ya está en la base y la próxima corrida con
        # --continuar dirá "sin novedad". Hay que decirlo, o no se publica nunca.
        detail = str(getattr(err, "message", None) or err)
        if out["edition"]:
            detail += f" La edición {out['edition']['id']} quedó guardada: para escribir el sitio corré --solo-publicar."
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
            raise newspaper.NewspaperError("Indicá --archivo para importar, o un grupo ya importado con --grupo (nombre exacto) o --grupo-id.")

        if not a.solo_publicar:
            d_from, d_to = (newspaper.continue_range(con, chat["id"]) if a.continuar else
                            newspaper.resolve_range(con, chat["id"], a.desde, a.hasta, newspaper.DEFAULT_DAYS if a.dias is None else a.dias))
            if not llm.providers():
                raise llm.LLMError("No hay ningún modelo configurado en .env (ver .env.example).")
            readers = [newspaper.ALL if x.strip() == "todos" else x.strip() for x in a.lecturas.split(",") if x.strip()]
            res = newspaper.generate(con, chat["id"], chat["name"], d_from, d_to, editor=a.modelo or newspaper.AUTO, readers=readers,
                                     consent_chat=a.confirmo_enviar_chat, order=a.orden, max_articles=a.articulos,
                                     min_articles=newspaper.MIN_ARTICLES if a.continuar else 1)
            s = res["sent"]
            out["edition"] = {"id": res["id"], "from": d_from, "to": d_to, **{k: res[k] for k in ("articles", "socials", "tools", "unread", "readings")}, "sent": s}
            out["warnings"] = res["warnings"]
            say(f"Edición {res['id']} ({d_from} a {d_to}): {res['articles']} artículos, {res['socials']} redes, {res['tools']} sitios, "
                f"{res['unread']} sin leer, {res['readings']} lecturas.")
            say(f"Enviado: {s['pages']} páginas a {', '.join([*s['pages_tried'], s['pages_to']])}; " +
                (f"{s['chat_messages']} mensajes seudonimizados a {', '.join(s['chat_to'])}." if s["chat_messages"] else "nada del chat."))
            for w in res["warnings"]:
                say("Aviso:", w)
        if a.salida:
            files = newspaper.publish(con, chat["id"], chat["name"], a.salida)
            out["site"] = {"dir": str(a.salida), "files": len(files)}
            say(f"Sitio escrito en {a.salida}: {len(files) - 1} edición(es) + index.html")
        code, out["status"] = EXIT_OK, "publicada" if a.solo_publicar else "generada"
    except (newspaper.NoNews, newspaper.NoMaterial) as err:
        # No es una falla: esta vez no hay nada nuevo. El sitio no se reescribe (para eso está --solo-publicar).
        code, out["status"], out["detail"] = EXIT_NOTHING, "sin_novedad" if isinstance(err, newspaper.NoNews) else "material_insuficiente", str(err)
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
