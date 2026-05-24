"""
SQLite 저장소 (vision + STT 통합).
tests/integration/results.db
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
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT    NOT NULL,
            description   TEXT,
            game          TEXT,
            video_file    TEXT,
            audio_file    TEXT,
            audio_offset_s REAL,
            vision_model  TEXT,
            stt_model     TEXT,
            lookback_s    REAL
        );

        CREATE TABLE IF NOT EXISTS rounds (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    INTEGER NOT NULL REFERENCES sessions(id),
            round_num     INTEGER NOT NULL,
            video_ts_s    REAL,                -- 비디오 타임라인 기준(초)
            image_b64     TEXT,                -- JPEG → base64
            vision_text   TEXT,                -- vision 분석 결과 (사용자 발화 합쳐서 호출한 응답)
            vision_elapsed_s REAL
        );

        CREATE TABLE IF NOT EXISTS transcripts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    INTEGER NOT NULL REFERENCES sessions(id),
            seg_num       INTEGER NOT NULL,
            -- 비디오 타임라인 기준 시각 (audio_offset 보정 후)
            video_start_s REAL NOT NULL,
            video_end_s   REAL NOT NULL,
            audio_wav     BLOB,
            transcript    TEXT,
            elapsed_s     REAL
        );

        -- round 와 transcript 의 다대다 관계 (어떤 transcript 가 어떤 round 의 컨텍스트로 쓰였는지)
        CREATE TABLE IF NOT EXISTS round_transcripts (
            round_id      INTEGER NOT NULL REFERENCES rounds(id),
            transcript_id INTEGER NOT NULL REFERENCES transcripts(id),
            PRIMARY KEY (round_id, transcript_id)
        );

        -- 라운드별 chat 호출 (가중치별로 1번씩)
        CREATE TABLE IF NOT EXISTS chat_calls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id      INTEGER NOT NULL REFERENCES rounds(id),
            weight        TEXT NOT NULL,            -- off | low | medium | high
            bots_csv      TEXT,                     -- 이번 호출에 참여한 봇 이름들
            elapsed_s     REAL,
            raw_response  TEXT                      -- 디버깅용 원본 응답
        );

        -- chat 호출 결과 메시지들
        CREATE TABLE IF NOT EXISTS chat_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_call_id  INTEGER NOT NULL REFERENCES chat_calls(id),
            bot_name      TEXT NOT NULL,
            message       TEXT NOT NULL
        );
        """)


def save_session(
    started_at: str,
    description: str,
    game: str,
    video_file: str,
    audio_file: str,
    audio_offset_s: float,
    vision_model: str,
    stt_model: str,
    lookback_s: float,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO sessions "
            "(started_at, description, game, video_file, audio_file, audio_offset_s, "
            " vision_model, stt_model, lookback_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, description, game, video_file, audio_file, audio_offset_s,
             vision_model, stt_model, lookback_s),
        )
        return cur.lastrowid


def save_transcript(
    session_id: int,
    seg_num: int,
    video_start_s: float,
    video_end_s: float,
    audio_wav: bytes,
    transcript: str,
    elapsed_s: float,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO transcripts "
            "(session_id, seg_num, video_start_s, video_end_s, audio_wav, transcript, elapsed_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, seg_num, video_start_s, video_end_s, audio_wav, transcript, elapsed_s),
        )
        return cur.lastrowid


def save_round(
    session_id: int,
    round_num: int,
    video_ts_s: float,
    image_bytes: bytes,
    vision_text: str,
    vision_elapsed_s: float,
    transcript_ids: list[int],
) -> int:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO rounds "
            "(session_id, round_num, video_ts_s, image_b64, vision_text, vision_elapsed_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, round_num, video_ts_s, b64, vision_text, vision_elapsed_s),
        )
        round_id = cur.lastrowid
        if transcript_ids:
            con.executemany(
                "INSERT INTO round_transcripts (round_id, transcript_id) VALUES (?, ?)",
                [(round_id, tid) for tid in transcript_ids],
            )
        return round_id


def save_chat_call(
    round_id: int,
    weight: str,
    bots_csv: str,
    elapsed_s: float,
    raw_response: str,
    messages: list[dict],
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO chat_calls (round_id, weight, bots_csv, elapsed_s, raw_response) "
            "VALUES (?, ?, ?, ?, ?)",
            (round_id, weight, bots_csv, elapsed_s, raw_response),
        )
        chat_call_id = cur.lastrowid
        if messages:
            con.executemany(
                "INSERT INTO chat_messages (chat_call_id, bot_name, message) VALUES (?, ?, ?)",
                [(chat_call_id, m["name"], m["message"]) for m in messages],
            )
        return chat_call_id


# ── 조회 ──────────────────────────────────────────────────────────────────────

def list_sessions() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()


def get_rounds(session_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM rounds WHERE session_id=? ORDER BY round_num", (session_id,)
        ).fetchall()


def get_transcripts(session_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM transcripts WHERE session_id=? ORDER BY seg_num", (session_id,)
        ).fetchall()


def get_round_transcripts(round_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT t.* FROM transcripts t "
            "JOIN round_transcripts rt ON rt.transcript_id = t.id "
            "WHERE rt.round_id = ? ORDER BY t.seg_num",
            (round_id,),
        ).fetchall()


def get_chat_calls(round_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM chat_calls WHERE round_id=? ORDER BY id",
            (round_id,),
        ).fetchall()


def get_chat_messages(chat_call_id: int) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM chat_messages WHERE chat_call_id=? ORDER BY id",
            (chat_call_id,),
        ).fetchall()
