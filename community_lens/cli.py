"""Periódico sin pantalla, para correr programado (cron, un agente, una acción de CI).

  python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --dias 7 --salida sitio/

Importa el chat (si se pasa --archivo), genera la edición del rango y escribe un sitio estático en --salida.
Enviar texto del chat a un modelo exige pedir --lecturas y además --confirmo-enviar-chat.

El rango, el máximo de artículos y a qué grupo se refiere un nombre los resuelve newspaper.py, con las mismas
reglas que usa la pantalla.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import importer, llm, newspaper
from .config import settings
from .db import connect


def _import(con, path: Path, chat, group: str | None, tz: str, date_order: str | None) -> int:
    """Importa el archivo en `chat` (el grupo ya resuelto por nombre o id) o, si no hay, donde corresponda."""
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
    if res["status"] == "duplicate":
        print(f"El archivo ya estaba importado (grupo {res['chat_id']}).")
    else:
        r = res["reconciliation"]
        print(f"Importado en el grupo {res['chat_id']}: {r['added']} registros nuevos, {r['matched_existing']} ya estaban.")
    return res["chat_id"]


def periodico(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m community_lens periodico", description="Genera una edición del periódico y la publica como sitio estático.")
    ap.add_argument("--archivo", type=Path, help="exportación de WhatsApp (.zip o .txt) a importar antes de generar")
    ap.add_argument("--grupo", help="nombre exacto del grupo en Community Lens (con --archivo, se crea si no existe)")
    ap.add_argument("--grupo-id", type=int, help="id del grupo, en lugar de --grupo; hace falta si dos grupos se llaman igual")
    ap.add_argument("--orden-fecha", choices=["MDY", "DMY", "YMD"], help="orden de fecha del archivo, si no se puede detectar")
    ap.add_argument("--dias", type=int, default=newspaper.DEFAULT_DAYS,
                    help=f"días que cubre la edición, contando hacia atrás desde el último día con mensajes ({newspaper.DEFAULT_DAYS})")
    ap.add_argument("--desde", help="AAAA-MM-DD; reemplaza a --dias")
    ap.add_argument("--hasta", help="AAAA-MM-DD (por defecto, el último día con mensajes)")
    ap.add_argument("--articulos", type=int, default=newspaper.LIMITS["article"][0],
                    help=f"máximo de artículos, de {newspaper.MIN_ARTICLES} a {newspaper.LIMITS['article'][1]} ({newspaper.LIMITS['article'][0]})")
    ap.add_argument("--orden", choices=["recent", "shared"], default="recent")
    ap.add_argument("--modelo", help="proveedor que redacta: anthropic, openai o gemini (por defecto, el primero configurado que responda)")
    ap.add_argument("--lecturas", default="", help="proveedores que dan su lectura de la conversación, separados por coma, o «todos» los configurados")
    ap.add_argument("--confirmo-enviar-chat", action="store_true", help="obligatorio con --lecturas: envía mensajes seudonimizados al modelo")
    ap.add_argument("--salida", type=Path, help="carpeta donde escribir el sitio estático (index.html + ediciones/)")
    ap.add_argument("--solo-publicar", action="store_true", help="no genera: solo reescribe el sitio con las ediciones guardadas")
    a = ap.parse_args(argv)

    con = connect()
    try:
        chat = newspaper.find_chat(con, a.grupo, a.grupo_id) if a.grupo or a.grupo_id is not None else None
        if a.archivo:
            chat = newspaper.find_chat(con, chat_id=_import(con, a.archivo, chat, a.grupo, settings().default_tz, a.orden_fecha))
        elif chat is None:
            raise newspaper.NewspaperError("Indicá --archivo para importar, o un grupo ya importado con --grupo (nombre exacto) o --grupo-id.")

        if not a.solo_publicar:
            d_from, d_to = newspaper.resolve_range(con, chat["id"], a.desde, a.hasta, a.dias)
            if not llm.providers():
                raise llm.LLMError("No hay ningún modelo configurado en .env (ver .env.example).")
            readers = [newspaper.ALL if x.strip() == "todos" else x.strip() for x in a.lecturas.split(",") if x.strip()]
            res = newspaper.generate(con, chat["id"], chat["name"], d_from, d_to, editor=a.modelo or newspaper.AUTO, readers=readers,
                                     consent_chat=a.confirmo_enviar_chat, order=a.orden, max_articles=a.articulos)
            s = res["sent"]
            print(f"Edición {res['id']} ({d_from} a {d_to}): {res['articles']} artículos, {res['socials']} redes, {res['tools']} sitios, "
                  f"{res['unread']} sin leer, {res['readings']} lecturas.")
            print(f"Enviado: {s['pages']} páginas a {', '.join([*s['pages_tried'], s['pages_to']])}; " +
                  (f"{s['chat_messages']} mensajes seudonimizados a {', '.join(s['chat_to'])}." if s["chat_messages"] else "nada del chat."))
            for w in res["warnings"]:
                print("Aviso:", w)
        if a.salida:
            files = newspaper.publish(con, chat["id"], chat["name"], a.salida)
            print(f"Sitio escrito en {a.salida}: {len(files) - 1} edición(es) + index.html")
    except (newspaper.NewspaperError, llm.LLMError, importer.ImportError_, ValueError) as err:
        print("No se pudo:", getattr(err, "message", None) or err, file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0
