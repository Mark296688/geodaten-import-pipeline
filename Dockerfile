# Geodaten-Import-Pipeline
# Stufe "basis": Laufzeit; Stufe "test": zusätzlich pytest und Tests; Standard (letzte Stufe): Betrieb.

FROM python:3.12-slim AS basis
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Berlin
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 10001 geoimport \
    && mkdir -p /app/berichte /app/config \
    && chown geoimport /app/berichte

FROM basis AS test
RUN pip install ".[test]"
COPY tests ./tests
COPY config ./config
USER geoimport
CMD ["pytest", "-v", "-p", "no:cacheprovider"]

FROM basis AS betrieb
USER geoimport
ENV GEOIMPORT_KONFIGURATION=/app/config
HEALTHCHECK --interval=5m --timeout=10s CMD ["geoimport", "pruefen"]
CMD ["geoimport", "zeitplan"]
