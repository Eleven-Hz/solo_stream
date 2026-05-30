"""
SQLite 저장소.
tests/stt/results.db 에 세션/segment/결과를 저장한다.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "output" / "results.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT    NOT NULL,
            description   TEXT,
            source        TEXT,        -- 'file:<path>' | 'mic' | 'video_audio:<path>'
            stt_model     TEXT,
            language      TEXT,
            vad_threshold REAL,
            sample_rate   INTEGER,
            total_duration_s REAL      -- 입력 오디오 총 길이
        );

        CREATE TABLE IF NOT EXISTS segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id),
            seg_num     INTEGER NOT NULL,
            start_s     REAL    NOT NULL,
            end_s       REAL    NOT NULL,
            audio_wav   BLOB                -- 16kHz mono PCM_16 WAV
        );

        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            segment_id  INTEGER NOT NULL REFERENCES segments(id),
            transcript  TEXT,
            elapsed_sec REAL
        );
        """)


def save_session(
    started_at: str,
    description: str,
    source: str,
    stt_model: str,
    language: str,
    vad_threshold: float,
    sample_rate: int,
    total_duration_s: float,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO sessions "
            "(started_at, description, source, stt_model, language, vad_threshold, sample_rate, total_duration_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, description, source, stt_model, language, vad_threshold, sample_rate, total_duration_s),
        )
        return cur.lastrowid


def save_segment(
    session_id: int,
    seg_num: int,
    start_s: float,
    end_s: float,
    audio_wav: bytes,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO segments (session_id, seg_num, start_s, end_s, audio_wav) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, seg_num, start_s, end_s, audio_wav),
        )
        return cur.lastrowid


def save_result(segment_id: int, transcript: str, elapsed_sec: float):
    with _conn() as con:
        con.execute(
            "INSERT INTO results (segment_id, transcript, elapsed_sec) VALUES (?, ?, ?)",
            (segment_id, transcript, elapsed_sec),
        )


# ── 조회 ──────────────────────────────────────────────────────────────────────

def list_sessions() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()


def get_session(session_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()


def get_segments(session_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM segments WHERE session_id=? ORDER BY seg_num", (session_id,)
        ).fetchall()


def get_results(segment_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM results WHERE segment_id=?", (segment_id,)
        ).fetchall()
