FROM python:3.12-slim

WORKDIR /app

# NC65 is read in oracledb THIN mode — no Oracle client needed here. (The
# WMS transaction-log mirror planned for phase 5 reads Flux, which is
# Oracle <=11g and needs THICK mode: copy mrp-api's Instant Client block then.)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY inventory-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inventory-api .

EXPOSE 8013

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8013"]
