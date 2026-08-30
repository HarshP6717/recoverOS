"""
RecoverOS Demo Data Reset Script.

Safely resets the SQLite event ledger and recovery journeys to start
from a clean, fully-audited dataset before live demonstrations.
"""

import sys
import os
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from backend.app.core.config import DATABASE_URL
from backend.app.models.database import init_db, engine


def reset_database() -> None:
    """Safely removes SQLite ledger database file(s) and reinitializes clean schema."""
    print("=" * 60)
    print("RECOVEROS DEMO DATA RESET")
    print("=" * 60)
    print(f"Target DATABASE_URL: {DATABASE_URL}")

    if not DATABASE_URL.startswith("sqlite"):
        print("[SKIP] DATABASE_URL is not SQLite. Skipping file removal.")
        return

    # Extract file path from sqlite connection URI
    # Typical formats: sqlite:///path/to/file.db or sqlite:////absolute/path/to/file.db
    clean_uri = DATABASE_URL.split("?")[0]
    raw_path = clean_uri.replace("sqlite:///", "").replace("sqlite://", "")

    if raw_path == ":memory:" or not raw_path:
        print("[INFO] In-memory SQLite database in use. Recreating schema...")
        init_db(engine)
        print("[SUCCESS] In-memory database reset successfully.")
        return

    db_path = Path(raw_path).resolve()
    print(f"Resolved SQLite DB Path: {db_path}")

    # Dispose existing connections before removing the file
    engine.dispose()

    # Remove database file and any SQLite journals/WALs
    files_to_remove = [
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    ]

    removed_count = 0
    for target in files_to_remove:
        if target.exists():
            try:
                target.unlink()
                print(f"[REMOVED] {target}")
                removed_count += 1
            except Exception as e:
                print(f"[WARNING] Could not remove {target}: {e}")

    if removed_count == 0:
        print("[INFO] No existing SQLite database files found to remove.")

    # Reinitialize clean schema tables
    init_db(engine)
    print("[SUCCESS] Clean schema initialized successfully.")
    print("Ready for live demo and judge walkthrough.")
    print("=" * 60)


if __name__ == "__main__":
    reset_database()
