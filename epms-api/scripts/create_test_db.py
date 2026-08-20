"""Create the test database (run once before running tests).

Usage:
    python -m scripts.create_test_db          # creates epms_test
    TEST_EPMS_DB=epms_test_x python -m scripts.create_test_db
"""
import os

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.core.config import settings


def create_test_db():
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    name = os.getenv("TEST_EPMS_DB", "epms_test")
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if cur.fetchone():
        print(f"{name} database already exists.")
    else:
        cur.execute(f'CREATE DATABASE "{name}" OWNER {settings.POSTGRES_USER}')
        print(f"{name} database created.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    create_test_db()
