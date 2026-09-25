FROM python:3.12-slim

WORKDIR /app

# ★ fonts-wqy-zenhei is not optional decoration. Every PDF this service makes
# is drawn with ReportLab, and ReportLab's built-in faces are Type 1 with no
# CJK glyphs — a Chinese name on a certificate comes out as a row of black
# boxes, with no error and no warning. Plenty of people at this plant have one.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repo root so the shared authz package can be installed.
COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY ehs-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ehs-api .

EXPOSE 8012

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012"]
