"""Esquema SQLite y conexión. Sin ORM: SQL explícito y auditable."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    timezone    TEXT NOT NULL,           -- zona usada para mostrar y agregar
    created_at  TEXT NOT NULL
);

-- Una importación = un archivo concreto incorporado a un chat.
CREATE TABLE IF NOT EXISTS imports (
    id                INTEGER PRIMARY KEY,
    chat_id           INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    file_name         TEXT NOT NULL,
    file_sha256       TEXT NOT NULL,
    file_bytes        INTEGER NOT NULL,
    imported_at       TEXT NOT NULL,
    date_order        TEXT NOT NULL,     -- MDY | DMY | YMD
    date_order_source TEXT NOT NULL,     -- 'detected' | 'user'
    clock             TEXT,
    has_seconds       INTEGER NOT NULL,
    timezone          TEXT NOT NULL,     -- zona declarada para las horas de ESTE archivo
    format_variant    TEXT,
    diagnostics       TEXT NOT NULL,     -- JSON del diagnóstico de importación
    reconciliation    TEXT NOT NULL,     -- JSON del resultado de reconciliación
    UNIQUE (chat_id, file_sha256)
);

-- Líneas que no se pudieron convertir en registro: nunca se descartan.
CREATE TABLE IF NOT EXISTS import_issues (
    id          INTEGER PRIMARY KEY,
    import_id   INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT NOT NULL,
    excerpt     TEXT NOT NULL
);

-- Un "autor" es un identificador tal como aparece en el archivo (alias),
-- no una persona. canonical_id registra una reconciliación manual.
CREATE TABLE IF NOT EXISTS authors (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    raw_label     TEXT NOT NULL,
    display_label TEXT NOT NULL,
    kind          TEXT NOT NULL,         -- phone | profile_name | contact_name | self
    canonical_id  INTEGER REFERENCES authors(id) ON DELETE SET NULL,
    UNIQUE (chat_id, raw_label)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY, -- ID interno estable
    uid             TEXT NOT NULL UNIQUE,-- referencia citable (CL-xxxx)
    chat_id         INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,    -- posición en la secuencia reconciliada del chat
    author_id       INTEGER REFERENCES authors(id) ON DELETE SET NULL,
    author_raw      TEXT,                -- autor tal como aparece
    ts_written      TEXT NOT NULL,       -- fecha/hora interpretada, reloj del archivo
    ts_utc          TEXT NOT NULL,
    ts_local        TEXT NOT NULL,       -- en la zona del chat
    record_type     TEXT NOT NULL,       -- message | system | unrecognized
    subtype         TEXT NOT NULL,
    text            TEXT NOT NULL,       -- texto original
    content_text    TEXT NOT NULL,       -- sin marcadores (búsqueda/clasificación)
    media_kind      TEXT,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    is_edited       INTEGER NOT NULL DEFAULT 0,
    is_poll         INTEGER NOT NULL DEFAULT 0,
    link_count      INTEGER NOT NULL DEFAULT 0,
    needs_review    INTEGER NOT NULL DEFAULT 0,
    review_reason   TEXT,
    match_key       TEXT NOT NULL,       -- clave de reconciliación (hash)
    first_import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_messages_chat_seq ON messages(chat_id, seq);
CREATE INDEX IF NOT EXISTS ix_messages_chat_ts ON messages(chat_id, ts_local);
CREATE INDEX IF NOT EXISTS ix_messages_author ON messages(author_id);

-- Dónde aparece cada mensaje en cada archivo importado.
CREATE TABLE IF NOT EXISTS message_sources (
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    import_id   INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    raw_ts      TEXT NOT NULL,
    PRIMARY KEY (message_id, import_id, line_start)
);

CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    domain      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_links_message ON links(message_id);
CREATE INDEX IF NOT EXISTS ix_links_domain ON links(domain);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, tokenize = "unicode61 remove_diacritics 2"
);

-- Ejecución de análisis: quién clasificó, con qué y cuándo.
CREATE TABLE IF NOT EXISTS analysis_runs (
    id               INTEGER PRIMARY KEY,
    chat_id          INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    provider         TEXT NOT NULL,      -- rules | jev
    model            TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    config           TEXT NOT NULL,      -- JSON (sin secretos)
    date_from        TEXT,
    date_to          TEXT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL,      -- running | completed | partial | failed
    n_messages       INTEGER NOT NULL DEFAULT 0,
    n_errors         INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);

-- Predicción original. Nunca se modifica al corregir.
CREATE TABLE IF NOT EXISTS classifications (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    assigned    INTEGER NOT NULL,        -- 1 si el proveedor asigna la categoría
    score       REAL,                    -- solo si el proveedor lo devuelve
    evidence    TEXT                     -- JSON: reglas disparadas, etc.
);
CREATE INDEX IF NOT EXISTS ix_class_run_cat ON classifications(run_id, category, assigned);
CREATE INDEX IF NOT EXISTS ix_class_msg ON classifications(message_id);

-- Correcciones humanas: historial de solo-agregado. La más reciente rige.
-- categories NULL = "volver a la clasificación automática".
CREATE TABLE IF NOT EXISTS human_labels (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    categories  TEXT,                    -- JSON array
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_human_msg ON human_labels(message_id);

-- Páginas enlazadas ya leídas (texto limpio), para no volver a descargarlas en cada edición.
CREATE TABLE IF NOT EXISTS pages (
    url         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,           -- article | social | tool
    status      TEXT NOT NULL,           -- ok | failed
    title       TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    reason      TEXT,
    fetched_at  TEXT NOT NULL
);

-- Ediciones del periódico. data = contenido estructurado; html = página autocontenida lista para compartir.
CREATE TABLE IF NOT EXISTS editions (
    id          INTEGER PRIMARY KEY,
    chat_id     INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    date_from   TEXT NOT NULL,
    date_to     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    editor      TEXT NOT NULL,           -- JSON: proveedor y modelo que redactó
    sent        TEXT NOT NULL,           -- JSON: qué salió de la máquina y hacia dónde
    data        TEXT NOT NULL,
    html        TEXT NOT NULL
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    p = Path(path or settings().db_path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: transacciones explícitas (BEGIN/COMMIT) donde importan
    con = sqlite3.connect(str(p), timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    # Migración: ejecución de clasificación vigente por chat (NULL = la más reciente).
    if "active_run_id" not in {r["name"] for r in con.execute("PRAGMA table_info(chats)")}:
        con.execute("ALTER TABLE chats ADD COLUMN active_run_id INTEGER")
    return con
