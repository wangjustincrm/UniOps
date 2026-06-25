"""Create the expense_test database. Run once before pytest.
Usage: python -m scripts.create_test_db
"""
import asyncio
import asyncpg
from app.core.config import settings


async def main():
    # Parse database_url to extract connection params
    # Format: postgresql+asyncpg://user:pass@host:port/dbname
    url = settings.database_url.replace("postgresql+asyncpg://", "")
    user_pass, rest = url.split("@", 1)
    user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
    host_port, dbname = rest.split("/", 1)
    host, port = (host_port.split(":", 1) if ":" in host_port else (host_port, "5432"))

    conn = await asyncpg.connect(
        host=host, port=int(port), user=user, password=password, database=dbname,
    )
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = 'expense_test'"
    )
    if exists:
        print("expense_test database already exists.")
    else:
        await conn.execute(f"CREATE DATABASE expense_test OWNER {user}")
        print("expense_test database created.")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
