# actions/file_controller.py
# File management — create, delete, move, rename, list, find, organize

import os
import time
import shutil
import difflib
import platform
import unicodedata
from collections import deque
from pathlib import Path
from datetime import datetime
import send2trash

from actions.open_app import shell_open
from config import protected


def _user_folders() -> dict[str, Path]:
    """Real locations of the user folders. On Windows they are often redirected (e.g. OneDrive\\Escritorio)."""
    home    = Path.home()
    folders = {
        "desktop":   home / "Desktop",
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "pictures":  home / "Pictures",
        "music":     home / "Music",
        "videos":    home / "Videos",
    }
    if platform.system() == "Windows":
        import winreg
        reg_names = {
            "desktop":   "Desktop",
            "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
            "documents": "Personal",
            "pictures":  "My Pictures",
            "music":     "My Music",
            "videos":    "My Video",
        }
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                for short, reg_name in reg_names.items():
                    try:
                        folders[short] = Path(os.path.expandvars(winreg.QueryValueEx(key, reg_name)[0]))
                    except OSError:
                        pass
        except OSError:
            pass
    return folders


_FOLDERS = _user_folders()
_FOLDER_ALIASES = {
    "escritorio": "desktop", "descargas": "downloads", "documentos": "documents",
    "imagenes": "pictures", "imágenes": "pictures", "fotos": "pictures",
    "musica": "music", "música": "music", "vídeos": "videos",
}
_SKIP_DIRS = {"appdata", "node_modules", "__pycache__", "site-packages", "venv", "$recycle.bin",
              "build", "dist", "target", "obj"}
_SEARCH_SECONDS = 8


def _get_desktop() -> Path:
    return _FOLDERS["desktop"]


def _resolve_path(raw: str) -> Path:
    """
    Resolves a path from user input.
    Supports shortcuts in English or Spanish: desktop/escritorio, downloads/descargas, documents, home...
    """
    lower = raw.strip().lower()
    lower = _FOLDER_ALIASES.get(lower, lower)
    if lower in _FOLDERS:
        return _FOLDERS[lower]
    if lower in ("home", ""):
        return Path.home()
    return Path(raw).expanduser()


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("_", " ").replace("-", " ").replace(".", " ").split())


def _match_score(query: str, name: str, is_dir: bool) -> float:
    """1.0 exact > 0.97 whole words > 0.95 word prefixes > 0.85 substring > fuzzy (typos), capped at 0.8."""
    stem = _norm(name if is_dir else Path(name).stem)
    if query in (stem, _norm(name)):
        return 1.0
    words = stem.split()
    if all(q in words for q in query.split()):
        return 0.97
    if all(any(w.startswith(q) for w in words) for q in query.split()):
        return 0.95
    if all(q in stem for q in query.split()):
        return 0.85
    ratio = max([difflib.SequenceMatcher(None, query, stem).ratio()] +
                [difflib.SequenceMatcher(None, query, w).ratio() for w in words])
    return 0.8 * ratio


def _search(name: str, extension: str = "", roots: list[Path] | None = None,
            max_results: int = 20) -> list[tuple[float, Path]]:
    """
    Fuzzy, accent-insensitive search by name (folders included unless an extension is given).
    Walks the roots breadth-first, skipping AppData/node_modules/hidden dirs, within a time budget.
    Stops early once a root yields an exact match.
    """
    query    = _norm(name)
    ext      = extension.lower() if not extension or extension.startswith(".") else f".{extension.lower()}"
    roots    = roots or [*_FOLDERS.values(), Path.home()]
    deadline = time.monotonic() + _SEARCH_SECONDS
    seen, found = set(), {}

    for root in roots:
        queue = deque([str(root)])
        while queue and time.monotonic() < deadline:
            folder = queue.popleft()
            if folder in seen:
                continue
            seen.add(folder)
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if is_dir and not entry.name.startswith(".") and entry.name.lower() not in _SKIP_DIRS:
                            queue.append(entry.path)
                        if ext and (is_dir or not entry.name.lower().endswith(ext)):
                            continue
                        score = _match_score(query, entry.name, is_dir) if query else 1.0
                        if score >= 0.6:
                            found[entry.path] = score
            except OSError:
                continue
        if any(s == 1.0 for s in found.values()):
            break

    ranked = sorted(((s, Path(p)) for p, s in found.items()), key=lambda t: (-t[0], len(t[1].parts)))
    return ranked[:max_results]


def open_file(name: str = "", path: str = "", app: str = "", extension: str = "") -> str:
    """
    Opens a file or folder by (approximate) name, optionally with a specific program.
    One clear match: opens it. Several: returns the candidates so the user can choose.
    """
    direct = Path(name or path).expanduser() if (name or path) else None
    if direct and direct.is_absolute() and direct.exists():
        target = direct
    elif not name and path:
        target = _resolve_path(path)
        if not target.exists():
            return f"Folder not found: {path}"
    elif not name:
        return "Tell me which file or folder to open."
    else:
        # Folder the user named first; everywhere else only if nothing is there.
        matches = _search(name, extension, [_resolve_path(path)], max_results=6) if path else []
        matches = matches or _search(name, extension, max_results=6)
        exact   = [p for s, p in matches if s == 1.0]
        if len(exact) == 1 or len(matches) == 1:
            target = exact[0] if len(exact) == 1 else matches[0][1]
        elif not matches:
            return (f"No file or folder matching '{name}' was found in the user folders. "
                    f"Tell the user and ask for another name or the folder where it is.")
        else:
            options = "\n".join(f"{i}. {p.name} — {p.parent}" for i, (_, p) in enumerate(matches, 1))
            return (f"No single exact match for '{name}'. Possible files:\n{options}\n"
                    f"Read these options to the user and ask which one to open. Then call "
                    f"file_controller action=open with path set to the chosen full path.")

    error = shell_open(str(target), app)
    if error:
        return f"Could not open {target.name}{' with ' + app if app else ''}: {error}"
    return f"Opened {target}{' with ' + app if app else ''}."


def _format_size(bytes_size: int) -> str:
    """Converts bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    """Lists files and folders in a directory."""
    try:
        target = _resolve_path(path)
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target}"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def _overwrite_question(target: Path) -> str:
    """Replacing an existing file loses its content: the user says yes first."""
    return (f"NEEDS CONFIRMATION. Nothing written yet: {target} already exists ({_format_size(target.stat().st_size)}) "
            "and writing replaces its content. Tell the user in one sentence and ask. Only after a clear yes, "
            "call again with the same fields and confirmed='yes'.")


def create_file(path: str, content: str = "", confirmed: bool = False) -> str:
    """Creates a new file with optional content."""
    try:
        target = Path(path).expanduser()
        if msg := protected(target):
            return msg
        if target.is_file() and not confirmed:
            return _overwrite_question(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"File created: {target.name}"
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str) -> str:
    """Creates a new folder (and parent folders if needed)."""
    try:
        target = Path(path).expanduser()
        if msg := protected(target):
            return msg
        target.mkdir(parents=True, exist_ok=True)
        return f"Folder created: {target}"
    except Exception as e:
        return f"Could not create folder: {e}"


def delete_file(path: str, confirm: bool = True) -> str:
    """
    Deletes a file or folder.
    Moves to Recycle Bin on Windows if possible, otherwise permanent delete.
    """
    try:
        target = Path(path).expanduser()
        if not target.exists():
            return f"Not found: {path}"
        if msg := protected(target):
            return msg

        try:

            send2trash.send2trash(str(target))
            return f"Moved to Recycle Bin: {target.name}"
        except ImportError:
            pass

        # Fallback: permanent delete
        if target.is_dir():
            shutil.rmtree(target)
            return f"Folder deleted permanently: {target.name}"
        else:
            target.unlink()
            return f"File deleted permanently: {target.name}"

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def move_file(source: str, destination: str) -> str:
    """Moves a file or folder to a new location."""
    try:
        src  = Path(source).expanduser()
        dst  = _resolve_path(destination)

        if not src.exists():
            return f"Source not found: {source}"

        if dst.is_dir():
            dst = dst / src.name

        if msg := protected(src) or protected(dst):
            return msg
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(source: str, destination: str) -> str:
    """Copies a file or folder."""
    try:
        src = Path(source).expanduser()
        dst = _resolve_path(destination)

        if not src.exists():
            return f"Source not found: {source}"

        if dst.is_dir():
            dst = dst / src.name

        if msg := protected(dst):
            return msg
        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        return f"Copied: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, new_name: str) -> str:
    """Renames a file or folder."""
    try:
        target   = Path(path).expanduser()
        new_path = target.parent / new_name

        if not target.exists():
            return f"Not found: {path}"
        if new_path.exists():
            return f"A file named '{new_name}' already exists."
        if msg := protected(target) or protected(new_path):
            return msg

        target.rename(new_path)
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, max_chars: int = 3000) -> str:
    """Reads and returns the content of a text file."""
    try:
        target = Path(path).expanduser()
        if not target.exists():
            return f"File not found: {path}"
        if not target.is_file():
            return f"Not a file: {path}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... (truncated, {len(content)} total chars)"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, content: str, append: bool = False, confirmed: bool = False) -> str:
    """Writes or appends content to a file."""
    try:
        target = Path(path).expanduser()
        if msg := protected(target):
            return msg
        if target.is_file() and not append and not confirmed:
            return _overwrite_question(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        return f"{action}: {target.name}"
    except Exception as e:
        return f"Could not write file: {e}"


def find_files(name: str = "", extension: str = "", path: str = "",
               max_results: int = 20) -> str:
    """
    Searches files and folders by approximate name and/or extension.
    No path: searches all user folders (Desktop, Downloads, Documents...) and home.
    """
    try:
        roots = None
        if path:
            roots = [_resolve_path(path)]
            if not roots[0].exists():
                return f"Search path not found: {path}"

        matches = _search(name, extension, roots, int(max_results))
        if not matches:
            return f"No {name or extension or 'files'} found."

        lines = []
        for _, item in matches:
            kind = "📁" if item.is_dir() else f"📄 ({_format_size(item.stat().st_size)})"
            lines.append(f"{kind} {item}")
        return f"Found {len(lines)} match(es), best first:\n" + "\n".join(lines)

    except Exception as e:
        return f"Search error: {e}"


def get_largest_files(path: str = "home", count: int = 10) -> str:
    """Returns the largest files in a directory."""
    try:
        search_path = _resolve_path(path)
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:\n"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    """Returns disk usage information."""
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        total  = _format_size(usage.total)
        used   = _format_size(usage.used)
        free   = _format_size(usage.free)
        pct    = usage.used / usage.total * 100

        return (
            f"Disk usage for {target}:\n"
            f"  Total : {total}\n"
            f"  Used  : {used} ({pct:.1f}%)\n"
            f"  Free  : {free}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    """
    Organizes the desktop by grouping files into folders by type.
    Creates folders: Images, Documents, Videos, Music, Archives, Others
    """
    try:
        desktop = _get_desktop()
        type_map = {
            "Images":    [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico"],
            "Documents": [".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"],
            "Videos":    [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
            "Music":     [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma"],
            "Archives":  [".zip", ".rar", ".7z", ".tar", ".gz"],
            "Code":      [".py", ".js", ".html", ".css", ".json", ".xml", ".ts", ".cpp", ".java"],
        }

        moved    = []
        skipped  = []

        for item in desktop.iterdir():

            if item.is_dir() or item.name.startswith("."):
                continue

            ext        = item.suffix.lower()
            target_dir = None

            for folder, extensions in type_map.items():
                if ext in extensions:
                    target_dir = desktop / folder
                    break

            if target_dir is None:
                target_dir = desktop / "Others"

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized. {len(moved)} files moved."
        if moved:
            result += "\n" + "\n".join(moved[:10])
            if len(moved) > 10:
                result += f"\n... and {len(moved)-10} more."
        if skipped:
            result += f"\n{len(skipped)} files skipped (already exist)."

        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str) -> str:
    """Returns detailed information about a file."""
    try:
        target = Path(path).expanduser()
        if not target.exists():
            return f"Not found: {path}"

        stat = target.stat()
        info = {
            "Name":     target.name,
            "Type":     "Folder" if target.is_dir() else "File",
            "Size":     _format_size(stat.st_size),
            "Location": str(target.parent),
            "Created":  datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "None",
        }

        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

def file_controller(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    action  = (parameters or {}).get("action", "").lower().strip()
    path    = (parameters or {}).get("path", "desktop")
    name    = (parameters or {}).get("name", "")
    content = (parameters or {}).get("content", "")
    confirmed = str((parameters or {}).get("confirmed", "")).lower() == "yes" or bool((parameters or {}).get("_trusted"))

    def _full_path(p: str, n: str) -> str:
        base = _resolve_path(p)
        if n:
            return str(base / n)
        return str(base)

    result = "Unknown action."

    try:
        if action == "list":
            result = list_files(path)

        elif action == "create_file":
            full = _full_path(path, name)
            result = create_file(full, content=content, confirmed=confirmed)

        elif action == "create_folder":
            full = _full_path(path, name)
            result = create_folder(full)

        elif action == "delete":
            full = _full_path(path, name)
            result = delete_file(full)

        elif action == "move":
            full = _full_path(path, name)
            result = move_file(full, parameters.get("destination", ""))

        elif action == "copy":
            full = _full_path(path, name)
            result = copy_file(full, parameters.get("destination", ""))

        elif action == "rename":
            full = _full_path(path, name)
            result = rename_file(full, parameters.get("new_name", ""))

        elif action == "read":
            full = _full_path(path, name)
            result = read_file(full)

        elif action == "write":
            full = _full_path(path, name)
            result = write_file(
                full,
                content=content,
                append=parameters.get("append", False),
                confirmed=confirmed
            )

        elif action == "open":
            result = open_file(name, parameters.get("path", ""),
                               parameters.get("app", ""), parameters.get("extension", ""))

        elif action == "find":
            result = find_files(
                name=name,
                extension=parameters.get("extension", ""),
                path=parameters.get("path", ""),
                max_results=parameters.get("max_results", 20)
            )

        elif action == "largest":
            result = get_largest_files(
                path=path,
                count=parameters.get("count", 10)
            )

        elif action == "disk_usage":
            result = get_disk_usage(path)

        elif action == "organize_desktop":
            result = organize_desktop()

        elif action == "info":
            full = _full_path(path, name)
            result = get_file_info(full)

        else:
            result = f"Unknown action: '{action}'"

    except Exception as e:
        result = f"File controller error: {e}"

    if player:
        player.write_log(f"[file] {result[:60]}")

    return result