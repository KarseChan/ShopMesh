"""Run Alembic migrations (upgrade to head).

Usage:
    python scripts/migrate.py          # upgrade to head
    python scripts/migrate.py --downgrade  # downgrade one revision
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_migrations():
    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Migration failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout or "Migrations applied successfully.")


def run_downgrade():
    cmd = [sys.executable, "-m", "alembic", "downgrade", "-1"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Downgrade failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout or "Downgrade applied successfully.")


if __name__ == "__main__":
    if "--downgrade" in sys.argv:
        run_downgrade()
    else:
        run_migrations()
