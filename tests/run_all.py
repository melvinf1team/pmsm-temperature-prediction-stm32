"""Run every check that needs no board: unit tests and consistency checks.

Usage::

    python tests/run_all.py
    python tests/run_all.py --verbose  # also print the output of passing checks
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent


def collect() -> list[Path]:
    return sorted((TESTS_DIR / "unit").glob("test_*.py")) + sorted((TESTS_DIR / "consistency").glob("validate_*.py"))


def run(script: Path) -> tuple[bool, float, str]:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return result.returncode == 0, time.monotonic() - start, (result.stdout + result.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every check that needs no board.")
    parser.add_argument("--verbose", action="store_true", help="Also print the output of passing checks.")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")

    scripts = collect()
    failures = []
    started = time.monotonic()
    for script in scripts:
        label = script.relative_to(TESTS_DIR).as_posix()
        print(f"  {label:<44}", end="", flush=True)
        ok, elapsed, output = run(script)
        print(f"{'PASS' if ok else 'FAIL'}  {elapsed:5.1f} s")
        if output and (args.verbose or not ok):
            print("\n".join(f"      {line}" for line in output.splitlines()))
        if not ok:
            failures.append(label)

    print(f"\n{len(scripts) - len(failures)}/{len(scripts)} passed in {time.monotonic() - started:.0f} s.")
    if failures:
        print("Failed: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
