#!/bin/sh
# SnapDock backend entrypoint:
# 1. If the DB already has tables but no alembic_version, stamp to avoid re-running
#    the initial migration on an existing install.
# 2. Run all pending Alembic migrations.
# 3. Start the uvicorn server.

set -e

python - <<'EOF'
import sys
import sqlalchemy as sa
from snapdock.config import settings

engine = sa.create_engine(settings.database_url)
insp = sa.inspect(engine)
tables = insp.get_table_names()

if "alembic_version" not in tables and "users" in tables:
    print("Existing install detected — stamping alembic head to skip initial migration.")
    import subprocess
    result = subprocess.run(["alembic", "stamp", "head"], check=True)
    sys.exit(result.returncode)
EOF

alembic upgrade head

exec python -m snapdock.main
