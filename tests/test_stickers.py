"""Stickers: lectura desde un .zip "con archivos", reconciliación con exportaciones sin archivos, ranking y borrado."""
import io
import zipfile

import pytest
from conftest import api_client, do_import
from test_contract import check

from community_lens import importer, stickers


def webp(tag: bytes) -> bytes:
    """Un .webp mínimo: la app solo verifica la cabecera RIFF/WEBP; `tag` hace distinto el contenido."""
    body = b"WEBPVP8L" + tag
    return b"RIFF" + len(body).to_bytes(4, "little") + body


A, B = webp(b"sonrisa"), webp(b"pulgar")

LINES = [
    ("[3/20/26, 10:00:00] Ana Ensayo: hola a todos", None),
    ("[3/20/26, 10:01:00] Beto Ensayo: ‎<attached: 00000002-STICKER-2026-03-20-10-01-00.webp>", ("00000002-STICKER-2026-03-20-10-01-00.webp", A)),
    ("[3/20/26, 10:02:00] Ana Ensayo: ‎<attached: 00000003-STICKER-2026-03-20-10-02-00.webp>", ("00000003-STICKER-2026-03-20-10-02-00.webp", A)),
    ("[3/20/26, 10:03:00] Beto Ensayo: ‎<attached: 00000004-STICKER-2026-03-20-10-03-00.webp>", ("00000004-STICKER-2026-03-20-10-03-00.webp", B)),
    ("[3/20/26, 10:04:00] Ana Ensayo: ‎<attached: 00000005-PHOTO-2026-03-20-10-04-00.jpg>", ("00000005-PHOTO-2026-03-20-10-04-00.jpg", b"\xff\xd8 foto")),
    # Nombre de sticker pero no es un .webp: no se guarda; el mensaje cuenta como sticker sin imagen.
    ("[3/20/26, 10:05:00] Beto Ensayo: ‎<attached: 00000006-STICKER-2026-03-20-10-05-00.webp>", ("00000006-STICKER-2026-03-20-10-05-00.webp", b"<html>no</html>")),
    ("[3/20/26, 10:06:00] Ana Ensayo: chau", None),
]
TXT = "\n".join(line for line, _ in LINES).encode()
# La misma conversación exportada "sin archivos".
TXT_NO_FILES = "\n".join(
    line.split(": ‎<attached:")[0] + (": ‎sticker omitted" if "STICKER" in line else ": ‎image omitted")
    if "<attached:" in line else line for line, _ in LINES).encode()


def zip_export(txt: bytes = TXT) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", txt)
        for _, f in LINES:
            if f:
                z.writestr(f[0], f[1])
    return buf.getvalue()


def import_zip(con, chat_id=None, data=None):
    data = data or zip_export()
    txt, name = importer.unzip_chat(data, "WhatsApp Chat - Ensayo.zip")
    pv = importer.preview(con, txt, name, "America/Costa_Rica", None, stickers=importer.zip_stickers(data))
    res = importer.commit(con, pv["token"], name, chat_id=chat_id, chat_name=None, tz_name="America/Costa_Rica", date_order=pv["date_order"])
    return pv, res


def test_zip_stickers_reads_only_real_webp_stickers():
    got = importer.zip_stickers(zip_export())
    assert set(got) == {"00000002-STICKER-2026-03-20-10-01-00.webp", "00000003-STICKER-2026-03-20-10-02-00.webp",
                        "00000004-STICKER-2026-03-20-10-03-00.webp"}
    assert importer.zip_stickers(TXT) == {}


def test_import_with_files_ranks_by_content(con):
    pv, res = import_zip(con)
    assert pv["stickers_in_file"] == 3 and res["stickers_added"] == 3
    r = stickers.ranking(con, res["chat_id"])
    assert (r["sticker_messages"], r["with_image"], r["distinct"]) == (4, 3, 2)
    top, second = r["items"]
    assert top["times"] == 2 and {s["label"] for s in top["senders"]} == {"Ana Ensayo", "Beto Ensayo"}
    assert second["times"] == 1 and second["senders"][0]["label"] == "Beto Ensayo"
    assert stickers.image(con, top["sha256"]) == A
    assert [s["label"] for s in r["senders"]] == ["Beto Ensayo", "Ana Ensayo"]  # 3 contra 1, con o sin imagen
    # La foto no se trató como sticker.
    kinds = [m["media_kind"] for m in con.execute("SELECT media_kind FROM messages WHERE media_kind IS NOT NULL ORDER BY seq")]
    assert kinds == ["sticker", "sticker", "sticker", "attachment", "sticker"]


def test_with_files_export_fills_images_of_an_earlier_export_without_files(con):
    first = do_import(con, "sin.txt", data=TXT_NO_FILES)
    cid = first["chat_id"]
    assert stickers.ranking(con, cid)["with_image"] == 0
    n = con.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    _, res = import_zip(con, chat_id=cid)
    assert res["reconciliation"]["added"] == 0 and res["stickers_added"] == 3
    assert con.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == n  # no se duplicó nada
    assert [it["times"] for it in stickers.ranking(con, cid)["items"]] == [2, 1]


def test_same_txt_already_imported_only_adds_stickers(con):
    plain = do_import(con, "chat.txt", data=TXT)
    _, res = import_zip(con, chat_id=plain["chat_id"])
    assert res["status"] == "duplicate" and res["stickers_added"] == 3
    _, again = import_zip(con, chat_id=plain["chat_id"])
    assert again["status"] == "duplicate" and again["stickers_added"] == 0


def test_delete_chat_keeps_images_still_used_elsewhere(con):
    _, one = import_zip(con)
    _, two = import_zip(con, data=zip_export(TXT.replace(b"hola a todos", b"otro grupo")))
    assert one["chat_id"] != two["chat_id"]
    assert con.execute("SELECT COUNT(*) c FROM sticker_images").fetchone()["c"] == 2
    importer.delete_chat(con, one["chat_id"])
    assert con.execute("SELECT COUNT(*) c FROM sticker_images").fetchone()["c"] == 2
    importer.delete_chat(con, two["chat_id"])
    assert con.execute("SELECT COUNT(*) c FROM sticker_images").fetchone()["c"] == 0


@pytest.fixture
def client():
    with api_client() as c:
        yield c


def test_api_contract_and_image(client):
    pv = client.post("/api/imports/preview", params={"filename": "Ensayo.zip"}, content=zip_export())
    assert pv.status_code == 200, pv.text
    check("ImportPreview", pv.json())
    assert pv.json()["stickers_in_file"] == 3
    body = {"token": pv.json()["token"], "file_name": "Ensayo.txt", "chat_id": None, "chat_name": "Ensayo",
            "timezone": "America/Costa_Rica", "date_order": "MDY", "confirm_low_overlap": False}
    done = client.post("/api/imports/commit", json=body).json()
    check("ImportCommitImported", done)

    r = client.get("/api/stickers", params={"chat_id": done["chat_id"]})
    check("StickerRanking", r.json())
    sha = r.json()["items"][0]["sha256"]
    img = client.get(f"/api/stickers/{sha}")
    assert img.status_code == 200 and img.content == A
    assert img.headers["content-type"] == "image/webp" and img.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/stickers/" + "0" * 64).status_code == 404
    assert client.get("/api/stickers/no-es-un-hash").status_code == 422

    # Mismo archivo otra vez: duplicado, sin stickers nuevos, y el tipo también valida.
    pv2 = client.post("/api/imports/preview", params={"filename": "Ensayo.zip"}, content=zip_export()).json()
    dup = client.post("/api/imports/commit", json={**body, "token": pv2["token"], "chat_id": done["chat_id"]}).json()
    check("ImportCommitDuplicate", dup)
    assert dup["stickers_added"] == 0
