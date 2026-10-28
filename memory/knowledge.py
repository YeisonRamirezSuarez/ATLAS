"""
What ATLAS knows about the user, as a graph for the MEMORIA view (qml/memory.qml): facts from
long_term.json and Engram (with the time each was learned), the conversations they came from, and
the PIN that guards the view. Pure functions over the data, plus the two writes the view needs.
"""
import hashlib
import json
import math
import random
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import get_config, update_config
from memory import memory_manager as mm

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
# Each category owns a region of the sphere: facts cluster round its direction.
_DIRS = {"identity": (0, -1, 0), "notes": (0, 1, 0), "projects": (1, 0, 0),
         "preferences": (-1, 0, 0), "relationships": (0, 0, 1), "wishes": (0, 0, -1)}
_STOP = set("about after again algo almost along also always among antes aunque because before being cada como "
            "con cosas cual cuando desde donde during entre esta este esto from have into just like mas more "
            "most much muy otra otro para pero porque quiere quiero sobre solo some still such tambien tanto "
            "than that their them then there these they this those through tiene todo under user usuario "
            "very want wants what when where which while with would your".split())


# ── data ─────────────────────────────────────────────────────────────────────

def engram_export() -> dict:
    """Everything Engram holds for ATLAS (its JSON export); {} without Engram or on failure."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "export.json"
        if mm._engram("export", str(f)) is None or not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return {}


def _utc(stamp: str) -> float | None:
    """Engram's 'YYYY-MM-DD HH:MM:SS[.fff]' (UTC) as epoch seconds."""
    try:
        return datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _day(date: str) -> float | None:
    """long_term.json's 'YYYY-MM-DD' (local) as epoch seconds at midnight."""
    try:
        return datetime.strptime(date, "%Y-%m-%d").timestamp()
    except (TypeError, ValueError):
        return None


def _when(t: float | None, date_only: bool) -> str:
    if t is None:
        return "fecha desconocida"
    d = datetime.fromtimestamp(t)
    return f"{d:%d %b %Y}" if date_only else f"{d:%d %b %Y, %H:%M}"


def _place(direction, seed: str, spread: float, radius: float) -> tuple[float, float, float]:
    """A point near `direction` on a sphere of `radius`, the same every time for the same seed."""
    rnd = random.Random(seed)
    v = [direction[i] + rnd.uniform(-spread, spread) for i in range(3)]
    n = math.sqrt(sum(c * c for c in v)) or 1
    return tuple(round(c / n * radius, 4) for c in v)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñü]{5,}", text.lower()) if w not in _STOP}


def build_graph(memory: dict, export: dict) -> dict:
    """{"nodes", "links", "start", "end"} for qml/memory.qml.
    nodes: id, kind (you | fact | conv), cat, key, label, value, t (epoch s), when, archived, x, y, z,
    conv (id of the conversation it probably came from, or ""), excerpt. links: [i, j, kind]."""
    obs = [o for o in export.get("observations") or [] if isinstance(o, dict)]
    by_title: dict[str, list] = {}
    for o in obs:
        if o.get("type") in CATEGORIES:
            by_title.setdefault(o.get("title", ""), []).append(o)
    convs = sorted((o for o in obs if o.get("type") == "conversation" and _utc(o.get("created_at", ""))),
                   key=lambda o: _utc(o["created_at"]))

    facts = {}   # "cat/key" -> fact node
    for cat in CATEGORIES:
        for key, entry in (memory.get(cat) or {}).items():
            if isinstance(entry, dict) and "value" in entry:
                facts[f"{cat}/{key}"] = {"cat": cat, "key": key, "value": str(entry["value"]),
                                         "date": entry.get("updated"), "archived": False}
    for title, items in by_title.items():   # in Engram only: trimmed from long_term.json, still recalled
        cat, _, key = title.partition("/")
        if title not in facts and cat in CATEGORIES and key:
            last = max(items, key=lambda o: _utc(o.get("created_at", "")) or 0)
            value = str(last.get("content", ""))
            prefix = key.replace("_", " ") + ": "
            facts[title] = {"cat": cat, "key": key, "value": value[len(prefix):] if value.startswith(prefix) else value,
                            "date": None, "archived": True}

    nodes = [{"id": "you", "kind": "you", "cat": "", "key": "", "label": "TÚ", "value": "", "t": None,
              "when": "", "archived": False, "x": 0.0, "y": 0.0, "z": 0.0, "conv": "", "excerpt": ""}]
    links, conv_index = [], {}
    for title, f in sorted(facts.items()):
        first = min((_utc(o.get("created_at", "")) for o in by_title.get(title, [])), default=None, key=lambda t: t or 1e18)
        t, date_only = (first, False) if first else (_day(f["date"]), True)
        x, y, z = _place(_DIRS[f["cat"]], title, 0.75, 0.85)
        node = {"id": f"fact:{title}", "kind": "fact", "cat": f["cat"], "key": f["key"],
                "label": f["value"] if len(f["value"]) <= 32 else f["value"][:31] + "…", "value": f["value"], "t": t, "when": _when(t, date_only),
                "archived": f["archived"], "x": x, "y": y, "z": z, "conv": "", "excerpt": ""}
        # Engram stores no link: the conversation saved just before the fact (within 10 min) is its likely source.
        src = [c for c in convs if first and 0 <= first - _utc(c["created_at"]) <= 600]
        if src:
            c = src[-1]
            cid = f"conv:{c.get('id')}"
            conv_index.setdefault(cid, None)   # node index set once conversation nodes are added
            node["conv"] = cid
            node["excerpt"] = str(c.get("content", ""))[:240]
        nodes.append(node)

    # conversation nodes just outside the facts they fed
    for cid in list(conv_index):
        c = next(o for o in convs if f"conv:{o.get('id')}" == cid)
        fed = [n for n in nodes if n["conv"] == cid]
        d = tuple(sum(n[a] for n in fed) / len(fed) for a in "xyz")
        x, y, z = _place(d, cid, 0.18, 1.02)
        t = _utc(c["created_at"])
        conv_index[cid] = len(nodes)
        nodes.append({"id": cid, "kind": "conv", "cat": "", "key": "", "label": str(c.get("title", ""))[:60],
                      "value": "", "t": t, "when": _when(t, False), "archived": False, "x": x, "y": y, "z": z,
                      "conv": "", "excerpt": str(c.get("content", ""))[:240]})

    index = {n["id"]: i for i, n in enumerate(nodes)}
    fact_ids = [i for i, n in enumerate(nodes) if n["kind"] == "fact"]
    for i in fact_ids:
        links.append([0, i, "you"])
        if nodes[i]["conv"]:
            links.append([i, index[nodes[i]["conv"]], "conv"])
    words = {i: _words(nodes[i]["value"] + " " + nodes[i]["key"].replace("_", " ")) for i in fact_ids}
    for a in fact_ids:   # facts that share a meaningful word, across categories; at most 2 each
        mine = 0
        for b in fact_ids:
            if b > a and mine < 2 and nodes[a]["cat"] != nodes[b]["cat"] and words[a] & words[b]:
                links.append([a, b, "related"])
                mine += 1
    times = [n["t"] for n in nodes if n["t"]]
    return {"nodes": nodes, "links": links, "start": min(times, default=None), "end": max(times, default=None)}


def load_graph() -> dict:
    """The current graph; also returns the Engram export it came from, for deletes."""
    export = engram_export()
    return {**build_graph(mm.load_memory(), export), "_export": export}


# ── writes from the view ─────────────────────────────────────────────────────

def correct(fact_id: str, value: str) -> None:
    """New value for a fact; it goes through the normal path (long_term.json, Engram, ATLAS ID sync)."""
    cat, _, key = fact_id.removeprefix("fact:").partition("/")
    if cat in CATEGORIES and key and value.strip():
        mm.update_memory({cat: {key: {"value": value.strip()}}})


def forget_everywhere(fact_id: str, export: dict) -> int:
    """Deletes a fact from long_term.json (and the user's other devices through sync) and hard-deletes
    every Engram observation of it, so ATLAS cannot recall it by search either. Returns how many
    Engram observations were removed. Conversations that mention it are kept."""
    title = fact_id.removeprefix("fact:")
    cat, _, key = title.partition("/")
    mm.forget(key, cat)
    gone = 0
    for o in export.get("observations") or []:
        if o.get("title") == title and o.get("type") == cat and mm._engram("delete", str(o.get("id")), "--hard", project=False) is not None:
            gone += 1
    return gone


# ── PIN ──────────────────────────────────────────────────────────────────────

def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000).hex()


def _saved_pin() -> dict:
    try:
        return get_config().get("memory_pin") or {}
    except (OSError, ValueError):   # first boot: no api_keys.json yet
        return {}


def has_pin() -> bool:
    return bool(_saved_pin().get("hash"))


def set_pin(pin: str) -> bool:
    """Saves a salted hash of the PIN (never the PIN). False when it is shorter than 4 characters."""
    if len(pin) < 4:
        return False
    salt = secrets.token_bytes(16)
    update_config(memory_pin={"salt": salt.hex(), "hash": _hash(pin, salt)})
    return True


def check_pin(pin: str) -> bool:
    saved = _saved_pin()
    try:
        return secrets.compare_digest(_hash(pin, bytes.fromhex(saved["salt"])), saved["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def clear_pin() -> None:
    update_config(memory_pin=None)
