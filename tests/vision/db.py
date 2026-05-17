"""
SQLite 저장소.
tests/vision/results.db 에 세션/라운드/결과를 저장한다.
"""
import base64
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "results.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT    NOT NULL,
            description TEXT,
            game        TEXT,
            gemini_model TEXT,
            claude_model TEXT,
            source      TEXT    -- 'video:<file>' | 'image:<file>' | 'capture'
        );

        CREATE TABLE IF NOT EXISTS rounds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id),
            round_num   INTEGER NOT NULL,
            timestamp_sec REAL,          -- 영상 모드일 때 타임스탬프(초)
            image_b64   TEXT             -- JPEG → base64
        );

        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id    INTEGER NOT NULL REFERENCES rounds(id),
            model       TEXT    NOT NULL, -- 'gemini' | 'claude'
            text        TEXT,
            elapsed_sec REAL
        );
        """)


def save_session(
    started_at: str,
    description: str,
    game: str,
    gemini_model: str,
    claude_model: str,
    source: str,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO sessions (started_at, description, game, gemini_model, claude_model, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (started_at, description, game, gemini_model, claude_model, source),
        )
        return cur.lastrowid


def save_round(session_id: int, round_num: int, image_bytes: bytes, timestamp_sec: float | None = None) -> int:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO rounds (session_id, round_num, timestamp_sec, image_b64) VALUES (?, ?, ?, ?)",
            (session_id, round_num, timestamp_sec, b64),
        )
        return cur.lastrowid


def save_result(round_id: int, model: str, text: str, elapsed_sec: float):
    with _conn() as con:
        con.execute(
            "INSERT INTO results (round_id, model, text, elapsed_sec) VALUES (?, ?, ?, ?)",
            (round_id, model, text, elapsed_sec),
        )


# ── 조회 ──────────────────────────────────────────────────────────────────────

def list_sessions() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()


def get_session(session_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()


def get_rounds(session_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM rounds WHERE session_id=? ORDER BY round_num", (session_id,)
        ).fetchall()


def get_results(round_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM results WHERE round_id=?", (round_id,)
        ).fetchall()
