import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH, MONTHLY_SPARKS

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

_conn.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    sparks      INTEGER NOT NULL,
    last_reset  TEXT NOT NULL,
    referred_by INTEGER,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presentations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    topic      TEXT NOT NULL,
    slides     INTEGER NOT NULL,
    theme      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
""")
_conn.commit()


def _month(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def get_user(user_id: int, username: str | None = None) -> sqlite3.Row:
    """Возвращает пользователя, создаёт нового и пополняет Sparks раз в месяц."""
    now = datetime.now()
    with _lock:
        row = _conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            _conn.execute(
                "INSERT INTO users (user_id, username, sparks, last_reset, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, MONTHLY_SPARKS, now.isoformat(), now.isoformat()),
            )
            _conn.commit()
        elif _month(datetime.fromisoformat(row["last_reset"])) != _month(now):
            # Новый месяц: баланс поднимается до месячной нормы (купленное/бонусы сверх нормы не сгорают)
            _conn.execute(
                "UPDATE users SET sparks=MAX(sparks, ?), last_reset=? WHERE user_id=?",
                (MONTHLY_SPARKS, now.isoformat(), user_id),
            )
            _conn.commit()
        if username:
            _conn.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            _conn.commit()
        return _conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_new_user(user_id: int) -> bool:
    with _lock:
        return _conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None


def try_spend(user_id: int, amount: int) -> bool:
    """Атомарно списывает Sparks. False — если не хватает."""
    with _lock:
        cur = _conn.execute(
            "UPDATE users SET sparks = sparks - ? WHERE user_id=? AND sparks >= ?",
            (amount, user_id, amount),
        )
        _conn.commit()
        return cur.rowcount == 1


def add_sparks(user_id: int, amount: int) -> None:
    with _lock:
        _conn.execute("UPDATE users SET sparks = sparks + ? WHERE user_id=?", (amount, user_id))
        _conn.commit()


def set_referrer(user_id: int, referrer_id: int) -> bool:
    with _lock:
        cur = _conn.execute(
            "UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL AND user_id != ?",
            (referrer_id, user_id, referrer_id),
        )
        _conn.commit()
        return cur.rowcount == 1


def referral_count(user_id: int) -> int:
    with _lock:
        return _conn.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,)).fetchone()[0]


def save_presentation(user_id: int, topic: str, slides: int, theme: str, content: dict) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO presentations (user_id, topic, slides, theme, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, topic, slides, theme, json.dumps(content, ensure_ascii=False), datetime.now().isoformat()),
        )
        _conn.commit()
        return cur.lastrowid


def get_presentation(pres_id: int, user_id: int) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM presentations WHERE id=? AND user_id=?", (pres_id, user_id)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["content"] = json.loads(data["content"])
    return data


def list_presentations(user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute(
            "SELECT id, topic, slides, theme, created_at FROM presentations WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def stats() -> tuple[int, int]:
    with _lock:
        users = _conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        pres = _conn.execute("SELECT COUNT(*) FROM presentations").fetchone()[0]
    return users, pres
