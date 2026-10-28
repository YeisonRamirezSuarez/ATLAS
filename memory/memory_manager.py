import json
import re
import shutil
import subprocess
from datetime import datetime
from threading import Lock, Thread
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
_update_lock     = Lock()   # load-modify-save of update_memory runs one at a time
MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200

# Engram (https://github.com/Gentleman-Programming/engram) keeps every fact forever,
# so facts trimmed from long_term.json stay searchable, and logs every conversation so
# Atlas builds up memories with use. Shipped in bin/ (installer); else from PATH; else skipped.
ENGRAM_BIN     = shutil.which("engram", path=str(BASE_DIR / "bin")) or shutil.which("engram")
ENGRAM_PROJECT = "mark-xxxix"
ENGRAM_ERROR   = ""   # why the last engram call failed, "" when it worked (boot diagnostic, UI)
# Engram's own update notice on stderr, never the reason a call failed.
_ENGRAM_NOISE  = re.compile(r"Update available|To update|go install|releases/latest")


def _engram(*args: str, project: bool = True) -> str | None:
    """CLI stdout, or None when engram is missing or the call failed (reason in ENGRAM_ERROR),
    so callers never mistake a failure for an empty result."""
    global ENGRAM_ERROR
    if not ENGRAM_BIN:
        ENGRAM_ERROR = "engram not installed"
        return None
    try:
        r = subprocess.run(
            [ENGRAM_BIN, *args, *(("--project", ENGRAM_PROJECT) if project else ())],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        ENGRAM_ERROR = str(e)
    else:
        if r.returncode == 0:
            ENGRAM_ERROR = ""
            return r.stdout.strip()
        lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip() and not _ENGRAM_NOISE.search(l)]
        ENGRAM_ERROR = lines[0] if lines else f"exit code {r.returncode}"
    print(f"[Memory] ⚠️ Engram {args[0]} failed: {ENGRAM_ERROR}")
    return None


def _engram_save(title: str, content: str, category: str) -> int | None:
    """Observation id once engram confirms the save, else None."""
    global ENGRAM_ERROR
    out = _engram("save", title, content, "--type", category)
    m = re.search(r"Memory saved: #(\d+)", out or "")
    if out is not None and not m:
        ENGRAM_ERROR = f"save not confirmed: {out[:120]}"
        print(f"[Memory] ⚠️ Engram {ENGRAM_ERROR}")
    return int(m.group(1)) if m else None


def _engram_save_async(title: str, content: str, category: str) -> None:
    # ponytail: CLI save appends, no upsert; old values stay with older dates. Use HTTP topic_key if it gets noisy.
    Thread(target=_engram_save, args=(title, content, category), daemon=True).start()


def diagnose() -> list[str]:
    """Boot check of the memory, one log line each. Engram gets a real round trip: a probe is
    saved, found and hard-deleted (only the probe; no other observation is touched)."""
    import time
    from uuid import uuid4
    lines = []
    try:
        mem = load_memory()
        lines.append(f"Archivo de memoria: {len(_all_entries(mem))} datos, "
                     f"{len(json.dumps(mem, ensure_ascii=False))}/{MEMORY_MAX_CHARS} caracteres.")
    except Exception as e:
        lines.append(f"Archivo de memoria: FALLÓ ({e}).")
    if not ENGRAM_BIN:
        return lines + ["Engram: NO INSTALADO — las conversaciones no se guardan a largo plazo."]
    version = _engram("version", project=False)
    stats = _engram("stats") or ""
    count = re.search(r"Observations:\s*(\d+)", stats)
    db = re.search(r"Database:\s*(.+)", stats)
    db = Path(db.group(1).strip()) if db else None
    size = f" ({db.stat().st_size / 1e6:.1f} MB)" if db and db.is_file() else ""
    doctor = (_engram("doctor") or "").splitlines()
    lines.append(f"Engram: {version or 'versión desconocida'}, "
                 f"{count.group(1) + ' recuerdos' + size if count else 'estadísticas no disponibles'}, "
                 f"doctor {doctor[0].split(':', 1)[-1].strip() if doctor else 'FALLÓ'}.")
    token, t0 = f"probe{uuid4().hex[:12]}", time.monotonic()
    obs = _engram_save("atlas boot probe", token, "diagnostic")
    found = obs is not None and token in (_engram("search", token, "--limit", "1") or "")
    gone = obs is not None and _engram("delete", str(obs), "--hard", project=False) is not None
    if obs is not None and found and gone:
        lines.append(f"Engram escribir/leer/borrar: ok ({(time.monotonic() - t0) * 1000:.0f} ms).")
    else:
        step = "escribir" if obs is None else "leer" if not found else f"borrar la prueba #{obs}"
        lines.append(f"Engram escribir/leer/borrar: FALLÓ al {step} ({ENGRAM_ERROR or 'no encontrado'}).")
    return lines


def save_conversation(user_text: str, atlas_text: str) -> None:
    _engram_save_async(user_text[:60], f"User: {user_text[:600]}\nAtlas: {atlas_text[:600]}", "conversation")


def recent_memories(max_chars: int = 1500) -> str:
    """Latest engram observations (facts and conversations) for the system prompt."""
    out = _engram("context")
    if not out:
        return ""
    if len(out) > max_chars:
        out = out[:max_chars - 1] + "…"
    return ("[RECENT MEMORIES — earlier conversations with this person. Let them shape "
            "your tone and references; call search_memory for anything older]\n" + out + "\n")


# Learned skills: scripts that solved a task. Engram indexes them (its search output
# truncates long content), the code itself lives in memory/skills/.
SKILLS_DIR = BASE_DIR / "memory" / "skills"
BUILTIN_SKILLS_DIR = BASE_DIR / "memory" / "builtin_skills"   # shipped with the app, no user data; not in engram


def save_skill(goal: str, plan: str, code: str) -> None:
    """Saves a script that ran and checked its own result. Never overwrites: each version is a new
    file, so an older one is still there to go back to. The output is not stored (it may be personal)."""
    import platform
    SKILLS_DIR.mkdir(exist_ok=True)
    name = f"{re.sub(r'[^a-z0-9]+', '_', goal.lower()).strip('_')[:40]}_{datetime.now():%Y%m%d%H%M%S}.py"
    verified = f"# VERIFIED: {datetime.now():%Y-%m-%d} on {platform.system()} {platform.version()}"
    (SKILLS_DIR / name).write_text(f"{code.rstrip()}\n{verified}\n", encoding="utf-8")
    _engram_save_async(f"skill: {goal[:80]}", f"file: {name}\n{plan}\n{verified[2:]}\nTask: {goal}", "skill")


def find_skills(goal: str, limit: int = 3) -> list[tuple[str, str]]:
    """(task, code) of learned skills similar to `goal`, best match first."""
    out = _engram("search", goal, "--type", "skill", "--match", "any", "--limit", str(limit)) or ""
    found = []
    for title, name in re.findall(r"— skill: (.*)\n\s*file: ([\w-]+\.py)", out):
        path = SKILLS_DIR / name
        if path.is_file():
            found.append((title.strip(), path.read_text(encoding="utf-8")))
    if len(found) < limit:
        # ponytail: word overlap with the name and PLAN line; fine for a few dozen builtin skills.
        words = set(re.findall(r"[a-z]{4,}", goal.lower()))
        scored = []
        for path in BUILTIN_SKILLS_DIR.glob("*.py"):
            code = path.read_text(encoding="utf-8")
            plan = code.split("\n", 1)[0].removeprefix("# PLAN:").strip()
            hits = len(words & set(re.findall(r"[a-z]{4,}", f"{path.stem} {plan}".lower())))
            if hits >= 2:
                scored.append((hits, plan, code))
        found += [(plan, code) for _, plan, code in sorted(scored, reverse=True)[:limit - len(found)]]
    return found


def search_memory(query: str) -> str:
    if not ENGRAM_BIN:
        return "Long-term memory search unavailable (engram not installed)."
    out = _engram("search", query, "--match", "any", "--limit", "10")
    if out is None:
        return f"Long-term memory search FAILED ({ENGRAM_ERROR}); this is not 'nothing found'."
    return out or f"No memories found for: {query}"


def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {}
    }


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()

    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            # Keep the unreadable file: the next save would otherwise overwrite it with empty memory.
            backup = MEMORY_PATH.with_name(f"long_term.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            try:
                MEMORY_PATH.replace(backup)
            except OSError:
                pass
            print(f"[Memory] ⚠️ Load error: {e} (file kept as {backup.name})")
            return _empty_memory()


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    serialized = json.dumps(memory, ensure_ascii=False)
    if len(serialized) <= MEMORY_MAX_CHARS:
        return memory

    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))

    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        print(f"[Memory] 🗑️  Trimmed {cat}/{key} (limit: {MEMORY_MAX_CHARS} chars)")

    return memory


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return

    memory = _trim_to_limit(memory)

    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        # Atomic: write a temp file, then rename over the old one (a crash never leaves half a file).
        tmp = MEMORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(MEMORY_PATH)


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict, path: str = "", changes: list | None = None) -> bool:
    """changes collects the (category, key, value) facts that changed, for memory sync."""
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value, f"{path}{key}/", changes):
                changed = True
        else:
            if isinstance(value, dict) and "value" in value:
                new_val = _truncate_value(str(value["value"]))
            else:
                new_val = _truncate_value(str(value))

            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
                title = f"{path}{key}"
                _engram_save_async(title, f"{key.replace('_', ' ')}: {new_val}", title.split("/")[0])
                if changes is not None and path.count("/") == 1:
                    changes.append((path[:-1], key, new_val))

    return changed


def _sync_queue(changes: list) -> None:
    """Hands changed facts to memory sync, outside _update_lock (sync takes that lock to apply pulls)."""
    try:
        from memory import sync
        for category, key, value in changes:
            sync.queue(category, key, value)
    except Exception as e:
        print(f"[Memory] ⚠️ Sync queue failed: {e}")


def update_memory(memory_update: dict, sync: bool = True) -> dict:
    """sync=False: a fact that came from memory sync, so it is not sent back."""
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    changes = []
    with _update_lock:
        memory = load_memory()
        if _recursive_update(memory, memory_update, changes=changes):
            save_memory(memory)
            print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    if sync:
        _sync_queue(changes)
    return memory


def should_extract_memory(user_text: str, atlas_text: str, api_key: str = "") -> bool:
    try:
        from or_client import client

        combined = f"User: {user_text[:300]}\nAtlas: {atlas_text[:1000]}"

        result = client.chat(
            f"Does this conversation contain ANY of the following?\n"
            f"- Personal facts (name, age, city, job, birthday, nationality)\n"
            f"- Preferences or favorites (food, color, music, sport, game, film, book, etc.)\n"
            f"- Active projects or goals the user is working on\n"
            f"- People in the user's life (friends, family, partner, colleagues)\n"
            f"- Things the user wants to do or buy in the future\n"
            f"- Any other fact worth remembering long-term\n\n"
            f"Reply only YES or NO.\n\nConversation:\n{combined}",
            system="You are a memory relevance checker. Reply only YES or NO.",
            max_tokens=50,   # thinking models spend tokens before the YES/NO
            temperature=0.0,
        )
        return "YES" in result.upper()

    except Exception as e:
        print(f"[Memory] ⚠️ Stage1 check failed: {e}")
        return False


def extract_memory(user_text: str, atlas_text: str, api_key: str = "") -> dict:
    try:
        from or_client import client

        combined = f"User: {user_text[:600]}\nAtlas: {atlas_text[:300]}"

        raw = client.chat(
            f"Extract ALL memorable personal facts from this conversation. Any language.\n"
            f"Return ONLY valid JSON. Use {{}} if truly nothing is worth saving.\n\n"
            f"Category guide:\n"
            f"  identity      → name, age, birthday, city, country, job, school, nationality, language\n"
            f"  preferences   → ANY favorite or preferred thing:\n"
            f"                  favorite_food, favorite_color, favorite_music, favorite_film,\n"
            f"                  favorite_game, favorite_sport, favorite_book, favorite_artist,\n"
            f"                  favorite_country, hobbies, interests, dislikes, etc.\n"
            f"  projects      → projects being built, ongoing work, goals, ideas in progress\n"
            f"                  (e.g. mark_xxv: 'Building an ATLAS-like AI assistant')\n"
            f"  relationships → people mentioned: friends, family, partner, colleagues\n"
            f"                  (e.g. best_friend_ali: 'Best friend, met in university')\n"
            f"  wishes        → future plans, things to buy, travel plans, dreams\n"
            f"  notes         → anything else worth remembering (habits, schedule, etc.)\n\n"
            f"IMPORTANT:\n"
            f"- Be LIBERAL: if something MIGHT be worth remembering, include it.\n"
            f"- Extract from BOTH user and Atlas turns.\n"
            f"- Skip: weather, reminders, search results, one-time commands.\n"
            f"- Use concise English values regardless of conversation language.\n\n"
            f"Format:\n"
            f'{{"identity":{{"name":{{"value":"Ali"}}}},\n'
            f' "preferences":{{"favorite_color":{{"value":"blue"}}}},\n'
            f' "projects":{{"mark_xxv":{{"value":"ATLAS-like AI assistant"}}}},\n'
            f' "relationships":{{"friend_yusuf":{{"value":"close friend"}}}},\n'
            f' "wishes":{{"buy_guitar":{{"value":"wants an acoustic guitar"}}}},\n'
            f' "notes":{{"works_at_night":{{"value":"usually active late at night"}}}}}}\n\n'
            f"Conversation:\n{combined}\n\nJSON:",
            system="Return ONLY valid JSON. No markdown, no explanation, no extra text.",
            max_tokens=1024,
            temperature=0.2,
        )

        clean = raw.strip()
        clean = re.sub(r"```(?:json)?", "", clean).strip().rstrip("`").strip()

        if not clean or clean == "{}":
            return {}

        return json.loads(clean)

    except json.JSONDecodeError:
        return {}
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ Extract failed: {e}")
        return {}


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"


STYLE_OPTIONS = {
    "humor":      ("off", "low", "normal", "high"),
    "tone":       ("usual", "professional", "witty", "focus", "empathic"),
    "formality":  ("casual", "normal", "formal"),
    "detail":     ("brief", "normal", "detailed"),
    "work_humor": ("off", "low", "normal"),
    "updates":    ("minimal", "normal", "detailed"),
}


def set_style(changes: dict, reset: bool = False) -> str:
    """How the user wants ATLAS to talk, set only on request. Kept apart from personal
    facts and skills, as plain strings so the size trim never drops it and Engram never gets it."""
    bad = []
    with _update_lock:
        memory = load_memory()
        style  = {} if reset else dict(memory.get("style") or {})
        for key, val in (changes or {}).items():
            val = str(val or "").strip().lower()
            if not val:
                continue
            if val in STYLE_OPTIONS.get(key, ()):
                style[key] = val
            else:
                bad.append(f"{key}={val}")
        if style != memory.get("style", {}):
            memory["style"] = style
            save_memory(memory)
    msg = f"Style now: {', '.join(f'{k}={v}' for k, v in style.items()) or 'defaults'}."
    if bad:
        allowed = "; ".join(f"{k}: {'/'.join(v)}" for k, v in STYLE_OPTIONS.items())
        msg += f" Rejected {', '.join(bad)}. Allowed: {allowed}."
    return msg


def format_style_for_prompt(memory: dict | None) -> str:
    style = (memory or {}).get("style") or {}
    if not style:
        return ""
    lines = "\n".join(f"  - {k.replace('_', ' ')}: {v}" for k, v in style.items())
    return ("[STYLE THE USER ASKED FOR — follow it over the defaults; the no-humor cases and "
            "warnings still win]\n" + lines + "\n")


def set_trusted(case: str, remove: bool = False) -> str:
    """Cases the user explicitly said need no confirmation (e.g. 'delete temp files'). Kept as a
    plain list, like style, so the size trim never drops one and only the user adds or removes them."""
    case = " ".join(str(case or "").split())
    with _update_lock:
        memory = load_memory()
        cases  = list(memory.get("trusted") or [])
        if case and remove:
            cases = [c for c in cases if c.lower() != case.lower()]
        elif case and case.lower() not in (c.lower() for c in cases):
            cases.append(case)
        if cases != memory.get("trusted", []):
            memory["trusted"] = cases
            save_memory(memory)
    return "No confirmation needed for: " + ("; ".join(f"'{c}'" for c in cases) or "nothing (always ask).")


def is_trusted(case: str) -> bool:
    case = " ".join(str(case or "").split()).lower()
    return bool(case) and case in (c.lower() for c in load_memory().get("trusted") or [])


def format_trusted_for_prompt(memory: dict | None) -> str:
    cases = (memory or {}).get("trusted") or []
    if not cases:
        return ""
    return ("[NO CONFIRMATION — the user asked not to be asked for these cases only. When an action clearly "
            "falls inside one, call the tool with trusted=<that case, exactly as written> instead of asking; "
            "anything else still needs the usual yes]\n" + "\n".join(f"  - {c}" for c in cases) + "\n")


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes", sync: bool = True) -> str:
    with _update_lock:
        memory = load_memory()
        cat    = memory.get(category, {})
        found  = key in cat
        if found:
            del cat[key]
            memory[category] = cat
            save_memory(memory)
    if not found:
        return f"Not found: {category}/{key}"
    if sync:
        _sync_queue([(category, key, None)])
    return f"Forgotten: {category}/{key}"

forget_memory = forget