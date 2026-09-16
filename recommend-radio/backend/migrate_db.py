"""One-shot schema migration, run before application workers start."""
import os

os.environ.pop('REQUIRE_DB_MIGRATION', None)

from database import init_db  # noqa: E402

if __name__ == '__main__':
    init_db()
