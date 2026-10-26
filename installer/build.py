"""Build a self-contained JARVIS installer for the OS this runs on.

    python installer/build.py

Run it with a relocatable interpreter (uv / python-build-standalone, Python 3.11),
ideally from the project's venv so its packages are reused instead of downloaded.
Output in dist/: Jarvis-Setup.exe (Windows, needs Inno Setup), Jarvis.dmg (macOS),
Jarvis-linux.tar.gz (Linux).
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
OUT  = DIST / "Jarvis"
OS   = platform.system()          # Windows | Darwin | Linux
ENGRAM_VERSION = "3.5.2"
VOSK_MODEL     = "vosk-model-small-es-0.42"   # wake word; must match wakeword.DEFAULT_MODEL
sys.path.insert(0, str(ROOT))
from config import VERSION   # noqa: E402  single version source
SKIP_DIRS  = {".venv", ".git", ".github", ".claude", "dist", "build", "installer", "__pycache__", "backups",
              "skills"}   # memory/skills: scripts this PC's Jarvis learned
SKIP_FILES = {"api_keys.json", "long_term.json", "jarvis.log", "Jarvis.exe", "launcher.cs",
              "jarvis_id.session", "sync.db",                    # the developer's JARVIS ID login and sync queue
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
    shutil.copytree(ROOT, app, ignore=lambda d, names: [
        n for n in names if n in SKIP_DIRS or n in SKIP_FILES or n.endswith(".pyc")])

    # 4. Chromium for browser_control (the headless shell is never used).
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(app / "ms-playwright")}
    run(py, "-m", "playwright", "install", "--no-shell", "chromium", env=env)

    # 5. engram CLI (long-term memory): bin/ was copied with the app; otherwise download it.
    # ffmpeg is left out: 440 MB for one optional tool (audio extraction), which says when it is missing.
    if not any((app / "bin").glob("engram*")):
        fetch_engram(app / "bin")

    # 6. Vosk Spanish model for the local wake word (~40 MB); models/ was copied if present.
    if not (app / "models" / VOSK_MODEL).is_dir():
        fetch_vosk_model(app / "models")


def fetch_vosk_model(dst):
    """Downloads and unpacks the Vosk wake word model over HTTPS."""
    # ponytail: alphacephei publishes no checksums; pin a sha256 here once one is recorded.
    import io, urllib.request, zipfile
    print("+ download", VOSK_MODEL, flush=True)
    data = urllib.request.urlopen(f"https://alphacephei.com/vosk/models/{VOSK_MODEL}.zip").read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    if any(n.startswith(("/", "..")) or ".." in Path(n).parts for n in zf.namelist()):
        sys.exit(f"Unsafe paths in {VOSK_MODEL}.zip")
    dst.mkdir(exist_ok=True)
    zf.extractall(dst)
    if not (dst / VOSK_MODEL).is_dir():
        sys.exit(f"{VOSK_MODEL}.zip did not contain {VOSK_MODEL}/")


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
    Image.open(ROOT / "jarvis.ico").save(dst)   # largest frame, 256 px
    return dst


def windows():
    # Same launcher as the dev Jarvis.exe: hidden console, so tools don't flash windows.
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    run(csc, "-nologo", "-target:winexe", f"-win32icon:{ROOT / 'jarvis.ico'}", "-r:System.Windows.Forms.dll",
        f"-out:{OUT / 'Jarvis.exe'}", ROOT / "launcher.cs")
    iscc = shutil.which("iscc") or next((p for p in (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
    ) if p.is_file()), None)
    if not iscc:
        sys.exit(f"Bundle ready in {OUT}. Install Inno Setup 6 to build Jarvis-Setup.exe.")
    run(iscc, f"/DAppVer={VERSION}", ROOT / "installer" / "jarvis.iss")


def mac():
    app = DIST / "Jarvis.app" / "Contents"
    shutil.rmtree(app.parent, ignore_errors=True)
    (app / "MacOS").mkdir(parents=True)
    shutil.move(OUT, app / "Resources")
    launcher = app / "MacOS" / "Jarvis"
    # The bundle stays read-only: code runs from Application Support, where it may
    # write its keys, memory and log. Chromium is used in place.
    launcher.write_text("""#!/bin/sh
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
D="$HOME/Library/Application Support/Jarvis"
mkdir -p "$D"
rsync -a --exclude ms-playwright --exclude jarvis.log "$R/app/" "$D/"
export PLAYWRIGHT_BROWSERS_PATH="$R/app/ms-playwright"
exec "$R/python/bin/python3" "$D/jarvis.pyw" "$@"
""")
    launcher.chmod(0o755)
    (app / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>JARVIS</string>
<key>CFBundleIdentifier</key><string>com.markxxxix.jarvis</string>
<key>CFBundleExecutable</key><string>Jarvis</string>
<key>CFBundleIconFile</key><string>jarvis.icns</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{VERSION}</string>
<key>NSMicrophoneUsageDescription</key><string>JARVIS listens to your voice commands.</string>
<key>NSAppleEventsUsageDescription</key><string>JARVIS controls apps on request.</string>
<key>NSBluetoothAlwaysUsageDescription</key><string>JARVIS controls Bluetooth lights on request.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
""")
    iconset = DIST / "jarvis.iconset"
    iconset.mkdir(exist_ok=True)
    png = icon_png(DIST / "jarvis.png")
    for s in (16, 32, 128, 256):
        run("sips", "-z", s, s, png, "--out", iconset / f"icon_{s}x{s}.png")
    run("iconutil", "-c", "icns", iconset, "-o", app / "Resources" / "jarvis.icns")
    shutil.rmtree(iconset)
    dmg = DIST / "Jarvis.dmg"
    dmg.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "JARVIS", "-srcfolder", app.parent, "-ov", "-format", "UDZO", dmg)


def linux():
    icon_png(OUT / "app" / "jarvis.png")
    (OUT / "install.sh").write_text("""#!/bin/sh
# Installs JARVIS for the current user (no root needed).
set -e
D="$HOME/.local/share/jarvis"
mkdir -p "$D" "$HOME/.local/share/applications"
cp -a "$(dirname "$0")/python" "$(dirname "$0")/app" "$D/"   # upgrades keep keys and memory
cat > "$HOME/.local/share/applications/jarvis.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=JARVIS
Exec="$D/python/bin/python3" "$D/app/jarvis.pyw"
Icon=$D/app/jarvis.png
Terminal=false
Categories=Utility;
EOF
echo "Installed. Open JARVIS from your app menu."
""")
    (OUT / "install.sh").chmod(0o755)
    run("tar", "-czf", DIST / "Jarvis-linux.tar.gz", "-C", DIST, "Jarvis")


if __name__ == "__main__":
    bundle()
    {"Windows": windows, "Darwin": mac, "Linux": linux}[OS]()
    print("Done:", DIST)
