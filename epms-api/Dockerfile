FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg / psycopg2 builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY epms-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY epms-api .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
