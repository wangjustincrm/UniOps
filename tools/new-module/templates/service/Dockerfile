FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY %%service%%/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY %%service%% .

EXPOSE %%api_port%%

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "%%api_port%%"]
