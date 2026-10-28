# Entry point for Atlas.exe: runs main.py with no visible console.
# Nobody sees stdout/stderr there, so output and crashes go to atlas.log.
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.stdout = sys.stderr = open("atlas.log", "w", encoding="utf-8", buffering=1)

# Chromium the installer ships next to the code (installer/build.py).
if os.path.isdir("ms-playwright"):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.abspath("ms-playwright"))

import main

main.main()
