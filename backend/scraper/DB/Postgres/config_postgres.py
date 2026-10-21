import os
from pathlib import Path

from dotenv import load_dotenv

# The password is read from the environment or from backend/scraper/.env.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_USERNAME = "scraper"
DATABASE_PASSWORD = os.environ.get("KNIVES_DB_PASSWORD", "")
if not DATABASE_PASSWORD:
    raise RuntimeError(
        "KNIVES_DB_PASSWORD isn't set, add it to backend/scraper/.env or the environment."
    )
DATABASE_PORT = 5432
DATABASE_URL = f"postgresql+psycopg2://{DATABASE_USERNAME}:{DATABASE_PASSWORD}@localhost:{DATABASE_PORT}/knives"
