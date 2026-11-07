import os

from server.api import from_environment

if not os.environ.get("DATABASE_URL", "").startswith(("postgresql://", "postgresql+psycopg://")):
    raise RuntimeError("The hosted license server requires PostgreSQL")

app = from_environment()
