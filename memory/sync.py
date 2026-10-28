"""
Shared memory across the devices of one ATLAS ID: the facts of long_term.json, nothing else
(conversations, style, no-confirmation cases and learned skills stay on this PC).

Offline first: every change waits in memory/sync.db until the server accepts it, so nothing
counts as synced before that. Each fact has a stable id "category/key" and a version; a device
that changed an old copy gets a conflict (the server copy is applied here, the local one is
kept in the conflicts table and in Engram), never a silent overwrite. Anything that looks like
a secret never leaves this PC. Engram stays the memory system: pulled facts go through
update_memory, which also files them in Engram.
"""
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH   = Path(__file__).resolve().parent / "sync.db"
FACTS     = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
KEY_RE    = re.compile(r"^[a-z0-9_]{1,120}$")   # same rule as the server's id check
OVERLAP   = timedelta(minutes=5)                # pulls re-read a little: a late commit is never missed
_SECRET   = re.compile(
    r"pass(word|wd|phrase)|contrase|clave|\bpin\b|api[\s_-]?key|secret|token|private[\s_-]?key|credential|"
    r"\bsk-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|-----BEGIN|\b(xox[abp]|ghp|gho|glpat)[-_][A-Za-z0-9]{10,}",
    re.I)
_lock = threading.Lock()


def is_secret(key: str, value: str) -> bool:
    return bool(_SECRET.search(f"{key} {value}"))


@contextmanager
def _db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.executescript("""
        create table if not exists queue (id text primary key, value text, deleted int, queued_at text);
        create table if not exists known (id text primary key, value text, deleted int, version int);
        create table if not exists conflicts (id text, local_value text, server_value text,
                                              server_version int, at text);
        create table if not exists meta (k text primary key, v text);""")
        yield con
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta(con, k, v=None):
    if v is None:
        row = con.execute("select v from meta where k=?", (k,)).fetchone()
        return row[0] if row else None
    con.execute("insert or replace into meta values (?, ?)", (k, str(v)))


def enabled() -> bool:
    try:
        from config import get_config
        return bool(get_config().get("memory_sync"))
    except Exception:
        return False


def queue(category: str, key: str, value: str | None) -> bool:
    """Waits for the next sync (value None = deleted). False when the fact never leaves this PC."""
    if category not in FACTS or not KEY_RE.match(key) or is_secret(key, value or "") or not enabled():
        return False
    with _lock, _db() as con:
        con.execute("insert or replace into queue values (?, ?, ?, ?)",
                    (f"{category}/{key}", value or "", int(value is None), _now()))
    return True


def _local_facts() -> dict[str, str]:
    from memory.memory_manager import _all_entries, load_memory
    return {f"{c}/{k}": str(e["value"]) for c, k, e in _all_entries(load_memory()) if c in FACTS}


def _apply(fact_id: str, value: str, deleted: bool) -> None:
    """Server copy into long_term.json (and Engram), without queueing it back."""
    from memory import memory_manager
    category, key = fact_id.split("/", 1)
    if deleted:
        memory_manager.forget(key, category, sync=False)
    else:
        memory_manager.update_memory({category: {key: {"value": value}}}, sync=False)


def _server_row(rest, fact_id: str) -> dict | None:
    rows = rest("GET", f"memories?id=eq.{fact_id}&select=id,value,deleted,version")
    return rows[0] if rows else None


def _settle(con, rest, fact_id: str, value: str, deleted: bool) -> str:
    """The server refused our version: take its copy, keep ours as a conflict when they differ."""
    row = _server_row(rest, fact_id)
    if row is None:
        return "retry"
    con.execute("insert or replace into known values (?, ?, ?, ?)",
                (fact_id, row["value"], int(row["deleted"]), row["version"]))
    if (row["value"], bool(row["deleted"])) == (value, deleted):
        return "same"
    con.execute("insert into conflicts values (?, ?, ?, ?, ?)",
                (fact_id, None if deleted else value, None if row["deleted"] else row["value"], row["version"], _now()))
    _apply(fact_id, row["value"], row["deleted"])
    return "conflict"


def _push(con, rest, device: str) -> tuple[int, int]:
    pushed = conflicts = 0
    for fact_id, value, deleted in con.execute("select id, value, deleted from queue").fetchall():
        deleted = bool(deleted)
        known = con.execute("select version from known where id=?", (fact_id,)).fetchone()
        category, key = fact_id.split("/", 1)
        body = {"value": value, "deleted": deleted, "device_id": device}
        from memory.atlas_id import IDError
        try:
            if known is None:
                rows = rest("POST", "memories", headers={"Prefer": "return=representation"},
                            json={**body, "id": fact_id, "category": category, "key": key, "version": 1})
            else:
                rows = rest("PATCH", f"memories?id=eq.{fact_id}&version=eq.{known[0]}",
                            headers={"Prefer": "return=representation"}, json={**body, "version": known[0] + 1})
        except IDError as e:
            if e.status != 409 and "version conflict" not in str(e):   # 409: the id exists already
                raise   # offline or refused: the queue keeps it for the next sync
            rows = []
        if rows:
            con.execute("insert or replace into known values (?, ?, ?, ?)", (fact_id, value, int(deleted), rows[0]["version"]))
            pushed += 1
        else:
            outcome = _settle(con, rest, fact_id, value, deleted)
            if outcome == "retry":
                continue
            conflicts += outcome == "conflict"
        con.execute("delete from queue where id=?", (fact_id,))
        con.commit()   # each accepted fact stays accepted even if a later one fails
    return pushed, conflicts


def _pull(con, rest) -> int:
    since = _meta(con, "last_pull")
    query = "memories?select=id,value,deleted,version,updated_at&order=updated_at.asc"
    if since:
        query += f"&updated_at=gte.{(datetime.fromisoformat(since) - OVERLAP).isoformat()}"
    pulled = 0
    for row in rest("GET", query.replace("+", "%2B")) or []:
        fact_id, since = row["id"], row["updated_at"]
        known = con.execute("select version from known where id=?", (fact_id,)).fetchone()
        waiting = con.execute("select 1 from queue where id=?", (fact_id,)).fetchone()
        if (known and known[0] >= row["version"]) or waiting:   # already have it / our push will settle it
            continue
        _apply(fact_id, row["value"], row["deleted"])
        con.execute("insert or replace into known values (?, ?, ?, ?)",
                    (fact_id, row["value"], int(row["deleted"]), row["version"]))
        con.commit()
        pulled += 1
    if since:
        _meta(con, "last_pull", since)
    return pulled


def sync_now() -> str:
    """Push what waits, then pull the other devices' changes. One line for the log."""
    from memory import atlas_id
    if not enabled():
        return "La sincronización de memoria está apagada."
    with _lock, _db() as con:
        try:
            if not atlas_id.touch_device():
                atlas_id.logout()
                raise atlas_id.IDError("this device was removed from the ATLAS ID account; signed out")
            if _meta(con, "seeded") != atlas_id.status()["user_id"]:   # first sync of this account here
                con.execute("delete from known")
                con.execute("delete from meta where k='last_pull'")
                for fact_id, value in _local_facts().items():
                    if KEY_RE.match(fact_id.split("/", 1)[1]) and not is_secret(fact_id, value):
                        con.execute("insert or ignore into queue values (?, ?, 0, ?)", (fact_id, value, _now()))
                _meta(con, "seeded", atlas_id.status()["user_id"])
                con.commit()
            device = atlas_id.device_id()
            pushed, conflicts = _push(con, atlas_id.rest, device)
            pulled = _pull(con, atlas_id.rest)
        except atlas_id.IDError as e:
            _meta(con, "last_error", str(e))
            _meta(con, "last_error_at", _now())
            return f"Sincronización de memoria FALLÓ: {e}. Los cambios esperan en la cola local."
        _meta(con, "last_sync", _now())
        _meta(con, "last_error", "")
        msg = f"Sincronización de memoria ok: {pushed} enviados, {pulled} recibidos"
        return msg + (f", {conflicts} conflictos (se mantuvo la copia del servidor, la tuya quedó guardada)." if conflicts else ".")


def stats() -> dict:
    """Numbers for the settings panel and the boot diagnostic."""
    with _lock, _db() as con:
        one = lambda q: con.execute(q).fetchone()[0]
        return {"pending": one("select count(*) from queue"), "synced": one("select count(*) from known where deleted=0"),
                "conflicts": one("select count(*) from conflicts"), "last_sync": _meta(con, "last_sync") or "",
                "last_error": _meta(con, "last_error") or ""}


def describe() -> str:
    """One line on ATLAS ID and sync for the boot log and the settings panel."""
    from memory import atlas_id
    st = atlas_id.status()
    if not st["configured"]:
        return "ATLAS ID: sin configurar (opcional); la memoria se queda en esta PC."
    if not st["signed_in"]:
        return "ATLAS ID: configurado, sin sesión iniciada; la memoria se queda en esta PC."
    if not enabled():
        return f"ATLAS ID: sesión de {st['email']}; sincronización apagada."
    s = stats()
    line = f"ATLAS ID: sesión de {st['email']}; sincronización activa, {s['synced']} sincronizados, {s['pending']} pendientes"
    return line + (f", último error: {s['last_error']}." if s["last_error"] else ".")


def recent_conflicts(limit: int = 5) -> list[tuple]:
    with _lock, _db() as con:
        return con.execute("select id, local_value, server_value, at from conflicts order by at desc limit ?",
                           (limit,)).fetchall()
