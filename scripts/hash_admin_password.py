"""Print the hash of a password for the admin panel (ADMIN_PASSWORD_HASH in .env).

Usage (from the repo root):
    uv run python scripts/hash_admin_password.py

Asks for the password twice without echoing it, and prints only the hash: paste it in .env
as ADMIN_PASSWORD_HASH=... The password itself is never stored anywhere.
"""

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.admin.auth import hash_password  # noqa: E402

MIN_LENGTH = 12


def main() -> int:
    password = getpass.getpass("Contraseña del panel: ")
    if len(password) < MIN_LENGTH:
        print(f"Usá al menos {MIN_LENGTH} caracteres.", file=sys.stderr)
        return 1
    if getpass.getpass("Repetila: ") != password:
        print("No coinciden.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
