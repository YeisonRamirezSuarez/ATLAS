"""
Version upgrades and update checks. JARVIS never installs releases (it rewrites its own code only on the user's
request, agent/self_improve.py): when the
version changes it backs up the user's data first, and it only *tells* the user about a newer
release. Installing one stays the user's decision (run the new installer).

  python updater.py check            newer release on GitHub? (needs "update_repo": "owner/name")
  python updater.py backup           zip keys + memory into backups/
  python updater.py restore <zip>    put a backup back (rollback after a bad upgrade)

api_keys.json (optional):
  "update_repo":  "owner/name"   # GitHub repo whose releases are checked; unset = no checks
"""
import json
import re
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKUPS  = BASE_DIR / "backups"
STAMP    = BASE_DIR / "memory" / ".version"     # version that last ran with this data
# User data an upgrade must keep. Code is never backed up or restored from here.
DATA     = ["config/api_keys.json", "config/telegram.session", "config/msal_cache.bin",
            "memory/long_term.json", "memory/skills", "memory/sync.db", "memory/jarvis_id.session"]
KEEP     = 10                                    # newest backups kept

# version -> function(base_dir) that rewrites user data for that version. Run in order, once.
MIGRATIONS: dict[str, callable] = {}


def parse(v) -> tuple[int, int, int] | None:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(v).strip())
    return tuple(map(int, m.groups())) if m else None


def backup(base: Path = BASE_DIR, label: str = "manual") -> Path | None:
    """Zips the user data that exists; None when there is none."""
    files = [f for d in DATA for f in ([base / d] if (base / d).is_file() else sorted((base / d).rglob("*")))
             if f.is_file()]
    if not files:
        return None
    out = base / "backups" / f"{datetime.now():%Y%m%d-%H%M%S}-{label}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(base).as_posix())
    for old in sorted(out.parent.glob("*.zip"))[:-KEEP]:
        old.unlink()
    return out


def restore(zip_path: Path, base: Path = BASE_DIR) -> list[str]:
    """Puts a backup back. Only paths under DATA are accepted, so a backup can't overwrite code."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = [n for n in names if ".." in Path(n).parts or not any(n == d or n.startswith(d + "/") for d in DATA)]
        if bad:
            raise ValueError(f"Not a JARVIS data backup (unexpected paths: {bad[:3]})")
        backup(base, "before-restore")   # a restore can itself be undone
        z.extractall(base)
    return names


def on_start(version: str, base: Path = BASE_DIR, log=print) -> None:
    """Call at startup: on a version change, back up the data, run pending migrations, stamp."""
    stamp = base / STAMP.relative_to(BASE_DIR)
    last = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    if last == version:
        return
    if last:   # first run ever has nothing to migrate
        saved = backup(base, f"before-{version}")
        log(f"SYS: Updated {last} -> {version}. Data backup: {saved or 'nothing to back up'}.")
        for v in sorted((v for v in MIGRATIONS if parse(last) < parse(v) <= parse(version)), key=parse):
            MIGRATIONS[v](base)   # an exception stops here: the stamp stays old, so it retries next start
    stamp.parent.mkdir(exist_ok=True)
    stamp.write_text(version, encoding="utf-8")


def check(version: str, repo: str | None, timeout: float = 10) -> str:
    """One-line answer about the latest GitHub release. Never downloads or installs anything."""
    if not repo:
        return "Update check off: set \"update_repo\": \"owner/name\" in api_keys.json."
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "JARVIS"})
    try:
        rel = json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        return f"Could not check for updates ({repo}): {e}"
    latest = parse(rel.get("tag_name", ""))
    if not latest:
        return f"Could not check for updates: release tag {rel.get('tag_name')!r} is not a version."
    if latest > parse(version):
        return f"Update available: {rel['tag_name']} (you have {version}). Download: {rel.get('html_url')}"
    return f"JARVIS {version} is up to date."


if __name__ == "__main__":
    sys.path.insert(0, str(BASE_DIR))
    from config import VERSION
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        try:
            from config import get_config
            repo = get_config().get("update_repo")
        except Exception:
            repo = None
        print(check(VERSION, repo))
    elif cmd == "backup":
        print(backup() or "No user data to back up.")
    elif cmd == "restore" and len(sys.argv) > 2:
        print("Restored:", *restore(Path(sys.argv[2])), sep="\n  ")
    else:
        sys.exit(__doc__)
