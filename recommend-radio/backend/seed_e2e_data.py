"""Seed a fresh Linux volume from an offline, user-authorized SQLite backup."""

import shutil
import sqlite3
from pathlib import Path

if __name__ == "__main__":
    source, target = Path("/source"), Path("/app/data")
    marker = target / ".seeded"
    if not marker.exists():
        if any((target / name).exists() for name in ("bili_radio.sqlite3", "amem.sqlite3")):
            raise RuntimeError("Refusing to overwrite a non-empty E2E data volume")
        for name in ("bili_radio.sqlite3", "amem.sqlite3"):
            original = sqlite3.connect((source / name).as_uri() + "?immutable=1", uri=True)
            backup = sqlite3.connect(target / name)
            try:
                original.backup(backup)
                assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                backup.close()
                original.close()
        for key in source.glob("*.auth.key"):
            shutil.copy2(key, target / key.name)
            (target / key.name).chmod(0o600)
        marker.write_text("Online-backup snapshot imported; never overwrite on restart.\n")
