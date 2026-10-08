FROM python:3.12-slim

WORKDIR /app

# fonts-wqy-zenhei: the reconciliation report embeds it so NC's Chinese bank
# names and voucher summaries render instead of printing as black boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY finance-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY finance-api .

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
