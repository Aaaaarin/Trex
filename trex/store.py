"""Durable state. Every step is written before it counts, so a crash loses nothing."""
import json
import sqlite3
import threading
import time
from typing import Any

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
create table if not exists runs(id text primary key, created real, request text, status text, data text);
create table if not exists events(seq integer primary key autoincrement, run text, ts real, kind text, data text);
create table if not exists evidence(id text primary key, run text, kind text, ref text, summary text, standin integer, data text);
create table if not exists outbox(key text primary key, run text, role text, recipient text, text text, status text, ts real, channel text, data text);
create table if not exists inbox(seq integer primary key autoincrement, run text, ts real, sender text, role text, text text, simulated integer, dedupe text unique, handled integer default 0, data text);
create table if not exists turns(seq integer primary key autoincrement, run text, role text, content text);
create table if not exists decisions(id text primary key, run text, ts real, question text, options text, answer text, answered_by text, evidence text);
create table if not exists kv(run text, key text, value text, primary key(run, key));
create table if not exists test_orders(key text primary key, run text, payload text, ts real);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("pragma journal_mode=wal")
            _conn.executescript(SCHEMA)
        return _conn


def reset_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def execute(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, args)


def rows(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def log(run: str, kind: str, **data: Any) -> None:
    execute("insert into events(run, ts, kind, data) values(?,?,?,?)", (run, time.time(), kind, json.dumps(data, default=str)))


def set_kv(run: str, key: str, value: Any) -> None:
    execute("insert into kv(run,key,value) values(?,?,?) on conflict(run,key) do update set value=excluded.value", (run, key, json.dumps(value, default=str)))


def get_kv(run: str, key: str, default: Any = None) -> Any:
    found = rows("select value from kv where run=? and key=?", (run, key))
    return json.loads(found[0]["value"]) if found else default


def add_evidence(run: str, kind: str, ref: str, summary: str, data: Any, standin: bool = False) -> str:
    """Record a fact and its origin. The same fact returns the same id, so retries add nothing."""
    existing = rows("select id from evidence where run=? and kind=? and ref=?", (run, kind, ref))
    if existing:
        execute("update evidence set data=?, summary=? where id=?", (json.dumps(data, default=str), summary, existing[0]["id"]))
        return existing[0]["id"]
    n = rows("select count(*) c from evidence where run=?", (run,))[0]["c"] + 1
    eid = f"{run}:E{n}"
    execute("insert into evidence(id, run, kind, ref, summary, standin, data) values(?,?,?,?,?,?,?)", (eid, run, kind, ref, summary, int(standin), json.dumps(data, default=str)))
    return eid


def get_evidence(eid: str) -> dict | None:
    found = rows("select * from evidence where id=?", (eid,))
    if not found:
        return None
    e = found[0]
    e["data"] = json.loads(e["data"])
    return e


def create_run(run: str, request: str) -> None:
    execute("insert or ignore into runs(id, created, request, status, data) values(?,?,?,?,?)", (run, time.time(), request, "working", "{}"))
    log(run, "request", text=request)


def set_status(run: str, status: str) -> None:
    execute("update runs set status=? where id=?", (status, run))
    log(run, "status", status=status)


def add_turn(run: str, role: str, content: Any) -> None:
    """History is append-only. Earlier turns are never edited."""
    execute("insert into turns(run, role, content) values(?,?,?)", (run, role, json.dumps(content, default=str)))


def get_turns(run: str) -> list[dict]:
    return [json.loads(r["content"]) for r in rows("select content from turns where run=? order by seq", (run,))]


def add_inbox(run: str, sender: str, role: str, text: str, simulated: bool, dedupe: str, data: Any = None) -> bool:
    """Late or repeated updates are safe: the same update twice counts once."""
    cur = execute("insert or ignore into inbox(run, ts, sender, role, text, simulated, dedupe, data) values(?,?,?,?,?,?,?,?)", (run, time.time(), sender, role, text, int(simulated), f"{run}|{dedupe}", json.dumps(data or {}, default=str)))
    added = cur.rowcount > 0
    log(run, "reply" if added else "duplicate_reply_ignored", sender=sender, role=role, text=text, simulated=simulated)
    return added


def unhandled_inbox(run: str) -> list[dict]:
    return rows("select * from inbox where run=? and handled=0 order by seq", (run,))


def mark_handled(seq_ids: list[int]) -> None:
    for s in seq_ids:
        execute("update inbox set handled=1 where seq=?", (s,))
