# PLAN: Read total, used, and available RAM plus usage percentage with psutil and list the top 5 processes by memory usage.
# RISK: safe

import sys
import subprocess

try:
    import psutil
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "psutil"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import psutil


def fmt(n):
    return f"{n / (1024 ** 3):.2f} GB"


vm = psutil.virtual_memory()
if vm.total <= 0:
    raise RuntimeError("Could not read memory information.")

used = vm.total - vm.available

print("RAM status:")
print(f"  Total:     {fmt(vm.total)}")
print(f"  Used:      {fmt(used)}")
print(f"  Available: {fmt(vm.available)}")
print(f"  Usage:     {vm.percent:.1f}%")

procs = {}
for p in psutil.process_iter(["pid", "name", "memory_info"]):
    try:
        mi = p.info.get("memory_info")
        if mi is None:
            continue
        name = p.info.get("name") or f"PID {p.info['pid']}"
        procs[p.info["pid"]] = (name, mi.rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        continue

if not procs:
    raise RuntimeError("Could not read process memory information.")

top = sorted(procs.items(), key=lambda kv: kv[1][1], reverse=True)[:5]

print("\nTop 5 processes by memory usage:")
for i, (pid, (name, rss)) in enumerate(top, 1):
    print(f"  {i}. {name} (PID {pid}): {rss / (1024 ** 2):.1f} MB")