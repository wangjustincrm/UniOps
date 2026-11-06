FROM python:3.12-slim

WORKDIR /app

# NC65 is read in oracledb THIN mode. The WMS transaction-history mirror reads
# Flux, which is Oracle <=11g: thin mode refuses it (DPY-3010), so THICK mode
# via the Instant Client — same block as mrp-api's Dockerfile.
# libaio1 is libaio1t64 on Debian trixie; Instant Client still dlopens the
# old SONAME, hence the symlink.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libaio1t64 unzip curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1

RUN curl -fsSL -o /tmp/ic.zip https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-linux.x64-19.28.0.0.0dbru.zip \
    && unzip -q /tmp/ic.zip -d /opt/oracle && rm /tmp/ic.zip \
    && echo /opt/oracle/instantclient_19_28 > /etc/ld.so.conf.d/oracle.conf && ldconfig
ENV ORACLE_CLIENT_LIB=/opt/oracle/instantclient_19_28

COPY packages/authz /packages/authz
RUN pip install --no-cache-dir /packages/authz

COPY inventory-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY inventory-api .

EXPOSE 8013

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8013"]
