FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repo root so the shared authz package can be installed.
COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY ehs-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ehs-api .

EXPOSE 8012

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012"]
