"""Build a self-contained ATLAS installer for the OS this runs on.

    python installer/build.py

Run it with a relocatable interpreter (uv / python-build-standalone, Python 3.11),
ideally from the project's venv so its packages are reused instead of downloaded.
Output in dist/: Atlas-Setup.exe (Windows, needs Inno Setup), Atlas.dmg (macOS),
Atlas-linux.tar.gz (Linux).
"""
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
OUT  = DIST / "Atlas"
OS   = platform.system()          # Windows | Darwin | Linux
ENGRAM_VERSION = "3.6.0"
VOSK_MODEL     = "vosk-model-small-es-0.42"   # wake word; must match wakeword.DEFAULT_MODEL
SPK_MODEL      = "vosk-model-spk-0.4"         # who is talking; must match speaker.SPK_MODEL
sys.path.insert(0, str(ROOT))
from config import VERSION   # noqa: E402  single version source
SKIP_DIRS  = {".venv", ".git", ".github", ".claude", ".superpowers", "dist", "build", "installer", "__pycache__", "backups",
              "skills",   # memory/skills: scripts this PC's Atlas learned
              "voices"}   # memory/voices: recordings of this PC's users
SKIP_FILES = {"api_keys.json", "long_term.json", "voices.json", "atlas.log", "Atlas.exe", "launcher.cs",
              "jarvis.log", "Jarvis.exe", "jarvis_id.session",   # same, from before 2.5.0
              "CLAUDE.md",                                       # developer notes, not for the installed app
              "atlas_id.session", "sync.db",                    # the developer's ATLAS ID login and sync queue
              "telegram.session", "telegram.session-journal",   # the developer's Telegram login
              "msal_cache.bin",                                  # the developer's Microsoft login
              ".version",                                        # updater stamp of this PC's data
              "Run.txt", "setup.py", "make_icon.py"}


def run(*cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def bundle():
    shutil.rmtree(OUT, ignore_errors=True)

    # 1. Interpreter: a copy of the standalone Python behind this venv.
    shutil.copytree(sys.base_prefix, OUT / "python", ignore=shutil.ignore_patterns("__pycache__", "EXTERNALLY-MANAGED"))
    py = OUT / "python" / ("python.exe" if OS == "Windows" else "bin/python3")

    # 2. Packages: reuse the venv's (fast, offline), then let pip fill any gap.
    rel = Path(sysconfig.get_path("purelib")).relative_to(sys.prefix)
    if sys.prefix != sys.base_prefix:
        shutil.copytree(Path(sys.prefix) / rel, OUT / "python" / rel, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "pip", "pip-*", "_virtualenv.*", "distutils-precedence.pth"))
    run(py, "-m", "ensurepip", "--upgrade")
    run(py, "-m", "pip", "install", "--no-warn-script-location", "-r", ROOT / "requirements.txt")

    # 3. App source, without the developer's keys, memory or logs.
    app = OUT / "app"
    # .bak_* are self-improve backups (one is a copy of api_keys.json); Atlas.spec a PyInstaller leftover.
    shutil.copytree(ROOT, app, ignore=lambda d, names: [
        n for n in names if n in SKIP_DIRS or n in SKIP_FILES or n.endswith(".pyc") or ".bak_" in n
        or n.startswith(("api_keys", "long_term", "sync.db")) or n in ("Atlas.spec", "atlas-hud.png")])

    # 4. Chromium for browser_control (the headless shell is never used).
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(app / "ms-playwright")}
    run(py, "-m", "playwright", "install", "--no-shell", "chromium", env=env)

    # 5. engram CLI (long-term memory): bin/ was copied with the app; otherwise download it.
    # ffmpeg is left out: 440 MB for one optional tool (audio extraction), which says when it is missing.
    exe = "engram.exe" if OS == "Windows" else "engram"   # a bin/ copied from another OS has the wrong one
    if not (app / "bin" / exe).is_file():
        fetch_engram(app / "bin")

    # 6. Vosk models (~40 MB wake word, ~13 MB speaker vectors); models/ was copied if present.
    for model in (VOSK_MODEL, SPK_MODEL):
        if not (app / "models" / model).is_dir():
            fetch_vosk_model(app / "models", model)


def fetch_vosk_model(dst, model):
    """Downloads and unpacks a Vosk model over HTTPS."""
    # ponytail: alphacephei publishes no checksums; pin a sha256 here once one is recorded.
    import io, urllib.request, zipfile
    print("+ download", model, flush=True)
    data = urllib.request.urlopen(f"https://alphacephei.com/vosk/models/{model}.zip").read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    if any(n.startswith(("/", "..")) or ".." in Path(n).parts for n in zf.namelist()):
        sys.exit(f"Unsafe paths in {model}.zip")
    dst.mkdir(exist_ok=True)
    zf.extractall(dst)
    if not (dst / model).is_dir():
        sys.exit(f"{model}.zip did not contain {model}/")


def fetch_engram(dst):
    """Downloads the pinned engram release for this OS and checks it against checksums.txt."""
    import hashlib, io, tarfile, urllib.request, zipfile
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    ext  = "zip" if OS == "Windows" else "tar.gz"
    name = f"engram_{ENGRAM_VERSION}_{OS.lower()}_{arch}.{ext}"
    base = f"https://github.com/Gentleman-Programming/engram/releases/download/v{ENGRAM_VERSION}"
    print("+ download", name, flush=True)
    data = urllib.request.urlopen(f"{base}/{name}").read()
    sums = urllib.request.urlopen(f"{base}/checksums.txt").read().decode()
    if f"{hashlib.sha256(data).hexdigest()}  {name}" not in sums:
        sys.exit(f"Checksum mismatch for {name}")
    exe = "engram.exe" if OS == "Windows" else "engram"
    dst.mkdir(exist_ok=True)
    if ext == "zip":
        (dst / exe).write_bytes(zipfile.ZipFile(io.BytesIO(data)).read(exe))
    else:
        (dst / exe).write_bytes(tarfile.open(fileobj=io.BytesIO(data)).extractfile(exe).read())
        (dst / exe).chmod(0o755)


def icon_png(dst):
    from PIL import Image   # in requirements.txt
    Image.open(ROOT / "atlas.ico").save(dst)   # largest frame, 256 px
    return dst


def windows():
    # Same launcher as the dev Atlas.exe: hidden console, so tools don't flash windows.
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    run(csc, "-nologo", "-target:winexe", f"-win32icon:{ROOT / 'atlas.ico'}", "-r:System.Windows.Forms.dll",
        f"-out:{OUT / 'Atlas.exe'}", ROOT / "launcher.cs")
    iscc = shutil.which("iscc") or next((p for p in (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
    ) if p.is_file()), None)
    if not iscc:
        sys.exit(f"Bundle ready in {OUT}. Install Inno Setup 6 to build Atlas-Setup.exe.")
    run(iscc, f"/DAppVer={VERSION}", ROOT / "installer" / "atlas.iss")


def mac():
    app = DIST / "Atlas.app" / "Contents"
    shutil.rmtree(app.parent, ignore_errors=True)
    (app / "MacOS").mkdir(parents=True)
    shutil.move(OUT, app / "Resources")
    launcher = app / "MacOS" / "Atlas"
    # The bundle stays read-only: code runs from Application Support, where it may
    # write its keys, memory and log. Chromium is used in place.
    launcher.write_text("""#!/bin/sh
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
D="$HOME/Library/Application Support/Atlas"
O="$HOME/Library/Application Support/Jarvis"   # before 2.5.0
[ -d "$O" ] && [ ! -d "$D" ] && mv "$O" "$D"
mkdir -p "$D"
rsync -a --exclude ms-playwright --exclude atlas.log "$R/app/" "$D/"
export PLAYWRIGHT_BROWSERS_PATH="$R/app/ms-playwright"
exec "$R/python/bin/python3" "$D/atlas.pyw" "$@"
""")
    launcher.chmod(0o755)
    (app / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>ATLAS</string>
<key>CFBundleIdentifier</key><string>com.atlas.assistant</string>
<key>CFBundleExecutable</key><string>Atlas</string>
<key>CFBundleIconFile</key><string>atlas.icns</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{VERSION}</string>
<key>NSMicrophoneUsageDescription</key><string>ATLAS listens to your voice commands.</string>
<key>NSAppleEventsUsageDescription</key><string>ATLAS controls apps on request.</string>
<key>NSBluetoothAlwaysUsageDescription</key><string>ATLAS controls Bluetooth lights on request.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
""")
    iconset = DIST / "atlas.iconset"
    iconset.mkdir(exist_ok=True)
    png = icon_png(DIST / "atlas.png")
    for s in (16, 32, 128, 256):
        run("sips", "-z", s, s, png, "--out", iconset / f"icon_{s}x{s}.png")
    run("iconutil", "-c", "icns", iconset, "-o", app / "Resources" / "atlas.icns")
    shutil.rmtree(iconset)
    dmg = DIST / "Atlas.dmg"
    dmg.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "ATLAS", "-srcfolder", app.parent, "-ov", "-format", "UDZO", dmg)


def linux():
    icon_png(OUT / "app" / "atlas.png")
    (OUT / "install.sh").write_text("""#!/bin/sh
# Installs ATLAS for the current user (no root needed).
set -e
D="$HOME/.local/share/atlas"
O="$HOME/.local/share/jarvis"   # before 2.5.0
[ -d "$O" ] && [ ! -d "$D" ] && mv "$O" "$D" && rm -f "$HOME/.local/share/applications/jarvis.desktop"
mkdir -p "$D" "$HOME/.local/share/applications"
cp -a "$(dirname "$0")/python" "$(dirname "$0")/app" "$D/"   # upgrades keep keys and memory
cat > "$HOME/.local/share/applications/atlas.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ATLAS
Exec="$D/python/bin/python3" "$D/app/atlas.pyw"
Icon=$D/app/atlas.png
Terminal=false
Categories=Utility;
EOF
echo "Installed. Open ATLAS from your app menu."
""")
    (OUT / "install.sh").chmod(0o755)
    run("tar", "-czf", DIST / "Atlas-linux.tar.gz", "-C", DIST, "Atlas")


if __name__ == "__main__":
    bundle()
    {"Windows": windows, "Darwin": mac, "Linux": linux}[OS]()
    print("Done:", DIST)
