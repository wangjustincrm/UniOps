FROM python:3.12-slim

WORKDIR /app

# fonts-wqy-zenhei: the approved-claim PDF embeds it so Chinese names and
# descriptions print as text, not black boxes (services/pdf_claim.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8006

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
