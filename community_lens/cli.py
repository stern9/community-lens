"""Periódico sin pantalla, para correr programado (cron, un agente, una acción de CI).

  python -m community_lens periodico --archivo export.zip --grupo "Mi comunidad" --dias 7 --salida sitio/

Importa el chat (si se pasa --archivo), genera la edición del rango y escribe un sitio estático en --salida.
Enviar texto del chat a un modelo exige pedir --lecturas y además --confirmo-enviar-chat.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import importer, llm, newspaper
from .config import settings
from .db import connect


def _import(con, path: Path, group: str | None, tz: str, date_order: str | None) -> int:
    data, name = importer.unzip_chat(path.read_bytes(), path.name)
    pv = importer.preview(con, data, name, tz, date_order)
    if pv["needs_date_order"]:
        raise SystemExit("No se pudo detectar el orden de fecha del archivo; indicalo con --orden-fecha MDY, DMY o YMD.")
    wanted = (group or pv["suggested_name"]).strip()
    by_name = con.execute("SELECT id FROM chats WHERE name=?", (wanted,)).fetchone()
    rec = pv["recommendation"]
    # Se actualiza el grupo con ese nombre; si no existe, el que el contenido reconoce; si no, se crea.
    chat_id = by_name["id"] if by_name else (rec["chat_id"] if rec and not group else None)
    res = importer.commit(con, pv["token"], name, chat_id=chat_id, chat_name=wanted, tz_name=tz, date_order=pv["date_order"])
    if res["status"] == "duplicate":
        print(f"El archivo ya estaba importado (grupo {res['chat_id']}).")
    else:
        r = res["reconciliation"]
        print(f"Importado en el grupo {res['chat_id']}: {r['added']} registros nuevos, {r['matched_existing']} ya estaban.")
    return res["chat_id"]


def periodico(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m community_lens periodico", description="Genera una edición del periódico y la publica como sitio estático.")
    ap.add_argument("--archivo", type=Path, help="exportación de WhatsApp (.zip o .txt) a importar antes de generar")
    ap.add_argument("--grupo", help="nombre del grupo en Community Lens (se crea si no existe)")
    ap.add_argument("--orden-fecha", choices=["MDY", "DMY", "YMD"], help="orden de fecha del archivo, si no se puede detectar")
    ap.add_argument("--dias", type=int, default=7, help="días que cubre la edición, contando hacia atrás desde el último mensaje (7)")
    ap.add_argument("--desde", help="AAAA-MM-DD; con --hasta, reemplaza a --dias")
    ap.add_argument("--hasta", help="AAAA-MM-DD")
    ap.add_argument("--articulos", type=int, default=8, help="máximo de artículos (8)")
    ap.add_argument("--orden", choices=["recent", "shared"], default="recent")
    ap.add_argument("--modelo", help="proveedor que redacta: anthropic, openai o gemini (por defecto, el primero configurado que responda)")
    ap.add_argument("--lecturas", default="", help="proveedores que dan su lectura de la conversación, separados por coma, o «todos» los configurados")
    ap.add_argument("--confirmo-enviar-chat", action="store_true", help="obligatorio con --lecturas: envía mensajes seudonimizados al modelo")
    ap.add_argument("--salida", type=Path, help="carpeta donde escribir el sitio estático (index.html + ediciones/)")
    ap.add_argument("--solo-publicar", action="store_true", help="no genera: solo reescribe el sitio con las ediciones guardadas")
    a = ap.parse_args(argv)

    con = connect()
    try:
        if a.archivo:
            chat_id = _import(con, a.archivo, a.grupo, settings().default_tz, a.orden_fecha)
        else:
            row = con.execute("SELECT id FROM chats WHERE name=?", (a.grupo or "",)).fetchone()
            if not row:
                raise SystemExit("Indicá --archivo para importar, o --grupo con el nombre exacto de un grupo ya importado.")
            chat_id = row["id"]
        chat = con.execute("SELECT name FROM chats WHERE id=?", (chat_id,)).fetchone()

        if not a.solo_publicar:
            if a.desde and a.hasta:
                d_from, d_to = a.desde, a.hasta
            else:
                last = con.execute("SELECT MAX(substr(ts_local,1,10)) d FROM messages WHERE chat_id=? AND record_type='message'", (chat_id,)).fetchone()["d"]
                if not last:
                    raise SystemExit("El grupo no tiene mensajes.")
                d_to = last
                d_from = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=max(a.dias, 1) - 1)).strftime("%Y-%m-%d")
            available = llm.providers()
            if not available:
                raise SystemExit("No hay ningún modelo configurado en .env (ver .env.example).")
            readers = [newspaper.ALL if x.strip() == "todos" else x.strip() for x in a.lecturas.split(",") if x.strip()]
            res = newspaper.generate(con, chat_id, chat["name"], d_from, d_to, editor=a.modelo or newspaper.AUTO, readers=readers,
                                     consent_chat=a.confirmo_enviar_chat, order=a.orden, max_articles=a.articulos)
            s = res["sent"]
            print(f"Edición {res['id']} ({d_from} a {d_to}): {res['articles']} artículos, {res['socials']} redes, {res['tools']} sitios, "
                  f"{res['unread']} sin leer, {res['readings']} lecturas.")
            print(f"Enviado: {s['pages']} páginas a {', '.join([*s['pages_tried'], s['pages_to']])}; " +
                  (f"{s['chat_messages']} mensajes seudonimizados a {', '.join(s['chat_to'])}." if s["chat_messages"] else "nada del chat."))
            for w in res["warnings"]:
                print("Aviso:", w)
        if a.salida:
            files = newspaper.publish(con, chat_id, chat["name"], a.salida)
            print(f"Sitio escrito en {a.salida}: {len(files) - 1} edición(es) + index.html")
    except (newspaper.NewspaperError, llm.LLMError, importer.ImportError_) as err:
        print("No se pudo:", getattr(err, "message", None) or err, file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0
