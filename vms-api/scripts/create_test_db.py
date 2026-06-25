"""Create the vms_test database (run once before running tests).

Usage:
    python -m scripts.create_test_db
"""
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.core.config import settings


def create_test_db() -> None:
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'vms_test'")
    if cur.fetchone():
        print("vms_test database already exists.")
    else:
        cur.execute(f"CREATE DATABASE vms_test OWNER {settings.POSTGRES_USER}")
        print("vms_test database created.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    create_test_db()
