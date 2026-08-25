"""
Lightweight SQLite storage — no ORM needed for this scale.

Tables:
  seen_pools     -> every pool we've evaluated, so we don't re-alert on the
                     same token every poll cycle
  chat_settings  -> per-chat filter overrides (market cap range, min score),
                     set via /setmcap and /setscore
  alerts         -> history of everything we actually alerted on (for /watchlist)
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_pools (
                    pool_address TEXT PRIMARY KEY,
                    first_seen_at INTEGER NOT NULL,
                    last_score REAL,
                    last_checked_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id TEXT PRIMARY KEY,
                    min_market_cap_usd REAL,
                    max_market_cap_usd REAL,
                    min_score_to_alert REAL,
                    registered_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_address TEXT NOT NULL,
                    symbol TEXT,
                    score REAL,
                    market_cap_usd REAL,
                    alerted_at INTEGER NOT NULL
                );
                """
            )

    # --- seen pools ---
    def mark_seen(self, pool_address: str, score: float):
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO seen_pools (pool_address, first_seen_at, last_score, last_checked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pool_address) DO UPDATE SET
                    last_score = excluded.last_score,
                    last_checked_at = excluded.last_checked_at
                """ ,
                (pool_address, now, score, now),
            )

    def already_alerted_recently(self, pool_address: str, cooldown_seconds: int = 3600) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT alerted_at FROM alerts WHERE pool_address = ? ORDER BY alerted_at DESC LIMIT 1",
                (pool_address,),
            ).fetchone()
        if not row:
            return False
        return (int(time.time()) - row["alerted_at"]) < cooldown_seconds

    def record_alert(self, pool_address: str, symbol: str, score: float, market_cap_usd: float):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO alerts (pool_address, symbol, score, market_cap_usd, alerted_at) VALUES (?, ?, ?, ?, ?)",
                (pool_address, symbol, score, market_cap_usd, int(time.time())),
            )

    def recent_alerts(self, limit: int = 10):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM alerts ORDER BY alerted_at DESC LIMIT ?", (limit,)
            ).fetchall()

    # --- chat settings ---
    def register_chat(self, chat_id: str, min_mcap: float, max_mcap: float, min_score: float):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_settings (chat_id, min_market_cap_usd, max_market_cap_usd, min_score_to_alert, registered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (chat_id, min_mcap, max_mcap, min_score, int(time.time())),
            )

    def update_mcap(self, chat_id: str, min_mcap: float, max_mcap: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE chat_settings SET min_market_cap_usd = ?, max_market_cap_usd = ? WHERE chat_id = ?",
                (min_mcap, max_mcap, chat_id),
            )

    def update_min_score(self, chat_id: str, min_score: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE chat_settings SET min_score_to_alert = ? WHERE chat_id = ?",
                (min_score, chat_id),
            )

    def get_chat_settings(self, chat_id: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
            ).fetchone()

    def all_registered_chats(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM chat_settings").fetchall()

    def total_pools_scanned(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM seen_pools").fetchone()
            return row["c"] if row else 0
