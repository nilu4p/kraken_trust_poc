FROM python:3.12-slim-bookworm AS runtime

ARG APP_UID=1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KEYS_DIR=/keys \
    PREUVES_DIR=/preuves \
    CACHE_DIR=/cache \
    DATA_DIR=/data \
    BACKUP_DIR=/backups

WORKDIR /app

# rasterio / numpy / cryptography / c2pa ship manylinux wheels: no compiler needed,
# but the GDAL bundled in the rasterio wheel links against the system libexpat.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import rasterio; print('rasterio', rasterio.__gdal_version__)" \
    && useradd --uid "${APP_UID}" --create-home --shell /usr/sbin/nologin kraken \
    && mkdir -p /keys /preuves /cache /data /backups \
    && chown kraken:kraken /keys /preuves /cache /data /backups

COPY --chown=root:root . /app

USER kraken
EXPOSE 8000

# X-Forwarded-For is evaluated by the app against TRUSTED_PROXIES only.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]

# Test image (make test): same runtime plus the test-only dependencies.
FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER kraken

# Production image (default target): no test tooling.
FROM runtime AS production
