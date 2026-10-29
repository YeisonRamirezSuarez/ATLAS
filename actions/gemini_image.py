"""Makes or edits images with the user's own Gemini web session (Google AI Pro, Nano Banana) through
gemini-webapi, and falls back to local FLUX (ComfyUI) when Gemini fails.

Not an official API. The Google session cookies come from ATLAS's CDP Chrome (the user signs in to
gemini.google.com there once), are kept in config/gemini_cookies.json and gemini-webapi refreshes them
in config/gemini_webapi/. Both paths are secrets and stay out of git.
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
COOKIES = BASE / "config" / "gemini_cookies.json"
os.environ.setdefault("GEMINI_COOKIE_PATH", str(BASE / "config" / "gemini_webapi"))
OUT_DIR = Path.home() / "Documents" / "ATLAS images"
CDP = "http://127.0.0.1:9222"
NAMES = ("__Secure-1PSID", "__Secure-1PSIDTS")
COMFY = Path.home() / "Documents" / "ATLAS design" / "comfyui"


def grab_cookies(cdp: str = CDP) -> dict:
    """Copies the Google session cookies from the CDP Chrome into config/gemini_cookies.json."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp, timeout=5000)
        jar = {c["name"]: c["value"] for ctx in browser.contexts
               for c in ctx.cookies("https://gemini.google.com") if c["name"] in NAMES}
    if "__Secure-1PSID" not in jar:
        raise RuntimeError("no Google session in the CDP Chrome: sign in to gemini.google.com there first")
    COOKIES.parent.mkdir(parents=True, exist_ok=True)
    COOKIES.write_text(json.dumps(jar), encoding="utf-8")
    return jar


async def _ask_gemini(prompt: str, files: list, jar: dict, stamp: str) -> tuple[list, str]:
    from gemini_webapi import GeminiClient
    client = GeminiClient(jar["__Secure-1PSID"], jar.get("__Secure-1PSIDTS"))
    await client.init(timeout=180, auto_refresh=False)
    try:
        out = await client.generate_content(prompt, files=files or None)
        saved = [await img.save(path=str(OUT_DIR), filename=f"{stamp}_{i}.png")
                 for i, img in enumerate(out.images)]
        return saved, out.text or ""
    finally:
        await client.close()


def gemini_images(prompt: str, files: list | None = None) -> tuple[list, str]:
    """Paths of the images Gemini made and its text reply. Re-reads the cookies from Chrome once if the saved ones fail."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        jar = json.loads(COOKIES.read_text(encoding="utf-8"))
        return asyncio.run(_ask_gemini(prompt, files or [], jar, stamp))
    except Exception:
        return asyncio.run(_ask_gemini(prompt, files or [], grab_cookies(), stamp))


def flux_images(prompt: str) -> list:
    """Local FLUX through ComfyUI; atlas_gen.py starts the server itself. Slow (2-3 min) and RAM heavy."""
    r = subprocess.run([str(COMFY / "venv" / "Scripts" / "python.exe"), str(COMFY / "atlas_gen.py"),
                        "--prompt", prompt, "--model", "flux", "--out", str(OUT_DIR),
                        "--name", time.strftime("%Y%m%d_%H%M%S")],
                       capture_output=True, text=True, timeout=900)
    paths = [line.split("  seed=")[0] for line in r.stdout.splitlines() if "  seed=" in line]
    if r.returncode or not paths:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "atlas_gen.py made no image")
    return paths


def generate_image(parameters: dict, player=None) -> str:
    """Tool entry: prompt, optional files (';'-separated images to edit), engine 'auto'|'gemini'|'flux'."""
    prompt = (parameters.get("prompt") or "").strip()
    if not prompt:
        return "Could not make the image: the prompt is empty."
    files = [f.strip() for f in (parameters.get("files") or "").split(";") if f.strip()]
    engine = (parameters.get("engine") or "auto").lower()
    why = ""
    if engine in ("auto", "gemini"):
        try:
            paths, text = gemini_images(prompt, files)
            if paths:
                return "Gemini made: " + "; ".join(paths)   # its text next to images is only placeholders
            why = f"Gemini sent no image. It said: {text[:400]}"
        except Exception as e:
            why = f"Gemini failed: {e}"
        if engine == "gemini" or files:   # FLUX here cannot edit an image
            return f"Could not make the image. {why}"
    try:
        paths = flux_images(prompt)
    except Exception as e:
        return f"Could not make the image. {why} Local FLUX failed too: {e}".strip()
    return ("Local FLUX made: " + "; ".join(paths) + (f"\n({why})" if why else "")).strip()
