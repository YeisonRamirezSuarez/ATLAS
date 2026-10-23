# PLAN: Find the genuine Google Chrome chrome.exe in Program Files, check its path and digital signature, open https://www.youtube.com with it, and confirm a chrome.exe process from that path is running.
# RISK: safe

import os
import sys
import subprocess
import time

URL = "https://www.youtube.com"

candidates = []
for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
    base = os.environ.get(env)
    if base:
        candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
candidates += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]

chrome = None
for c in candidates:
    p = os.path.abspath(c)
    if os.path.isfile(p) and os.path.basename(p).lower() == "chrome.exe":
        chrome = p
        break
if not chrome:
    raise RuntimeError("The genuine chrome.exe was not found in Program Files or Program Files (x86)")

pf_roots = [os.path.normcase(r"C:\Program Files"), os.path.normcase(r"C:\Program Files (x86)")]
if not any(os.path.normcase(chrome).startswith(r + os.sep) for r in pf_roots):
    raise RuntimeError(f"Refusing to launch: Chrome is not under Program Files: {chrome}")

ps = ("$s = Get-AuthenticodeSignature -LiteralPath '{0}'; "
      "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)").format(chrome.replace("'", "''"))
r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, text=True, timeout=60)
sig = (r.stdout or "").strip()
status, _, subject = sig.partition("|")
if status != "Valid" or "Google" not in subject:
    raise RuntimeError(f"Refusing to launch: chrome.exe signature is not a valid Google signature ({sig or r.stderr.strip()})")
print(f"Verified genuine Chrome: {chrome} (signature: {status}, {subject})")

subprocess.Popen([chrome, URL], close_fds=True)
time.sleep(3)

ps2 = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
       "ForEach-Object { $_.ExecutablePath }")
r2 = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps2],
                    capture_output=True, text=True, timeout=60)
paths = {os.path.normcase(l.strip()) for l in (r2.stdout or "").splitlines() if l.strip()}
if os.path.normcase(chrome) not in paths:
    raise RuntimeError("Chrome was launched but no chrome.exe process from the genuine path was found running")

print(f"Opened {URL} in genuine Google Chrome ({chrome}); chrome.exe is running.")
