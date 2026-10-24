# syntax=docker/dockerfile:1.7
# --- etap 1: budowa koła (wheel) ---
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY termin ./termin
# Za proxy z własnym CA: docker build --secret id=ca,src=/ścieżka/ca.crt .
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip wheel --wheel-dir /wheels .

# --- etap 2: obraz uruchomieniowy ---
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TERMIN_DATA_DIR=/data \
    TERMIN_LOG_JSON=true
RUN groupadd --system --gid 10001 termin \
 && useradd --system --uid 10001 --gid termin --home-dir /data --shell /usr/sbin/nologin termin \
 && mkdir -p /data && chown termin:termin /data
COPY --from=build /wheels /wheels
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install /wheels/*.whl && rm -rf /wheels
USER termin
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["termin", "serwer", "--host", "0.0.0.0", "--port", "8000", "--watcher"]
