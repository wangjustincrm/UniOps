FROM python:3.12-slim

WORKDIR /app

# libaio1 was renamed to libaio1t64 in Debian trixie (time_t transition) —
# the base python:3.12-slim image tracks trixie, so use the new package name.
# The shared object itself is also renamed (libaio.so.1t64), but the Oracle
# Instant Client binary dlopens the old SONAME "libaio.so.1" — symlink it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libaio1t64 unzip curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1

# WMS(Flux) Oracle server is <=11g: python-oracledb must run in thick mode
# (thin mode refuses <=11g with DPY-3010). 19c Instant Client is backward
# compatible with 11g.
RUN curl -fsSL -o /tmp/ic.zip https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-linux.x64-19.28.0.0.0dbru.zip \
    && unzip -q /tmp/ic.zip -d /opt/oracle && rm /tmp/ic.zip \
    && echo /opt/oracle/instantclient_19_28 > /etc/ld.so.conf.d/oracle.conf && ldconfig
ENV ORACLE_CLIENT_LIB=/opt/oracle/instantclient_19_28

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY mrp-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mrp-api .

EXPOSE 8011

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
