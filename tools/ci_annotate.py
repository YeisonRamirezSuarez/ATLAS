"""
Prints each failed or errored test of a `unittest -v` log as a GitHub Actions error annotation
(title: the test, message: the last lines of its traceback), so the run page shows why CI failed.

Run:  python tools/ci_annotate.py unittest.log
"""
import re
import sys

BLOCK = re.compile(r"^=+\n((?:ERROR|FAIL): [^\n]+)\n-+\n(.*?)(?=^=+\n|^-+\nRan )", re.S | re.M)


def annotations(log: str) -> list[str]:
    out = []
    for head, body in BLOCK.findall(log):
        tail = "%0A".join(line.strip() for line in body.strip().splitlines()[-4:])   # %0A: newline in annotations
        out.append(f"::error title={head.replace(':', ' -', 1)}::{tail[:1500]}")
    return out


if __name__ == "__main__":
    for line in annotations(open(sys.argv[1], encoding="utf-8", errors="replace").read()):
        print(line)
