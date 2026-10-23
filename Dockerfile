# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime

ARG VERSION=0.1.2
ARG REVISION=unknown

LABEL org.opencontainers.image.title="nzbget-sab-bridge" \
      org.opencontainers.image.description="Early NZBGet-compatible JSON-RPC bridge for SABnzbd" \
      org.opencontainers.image.source="https://github.com/kdmn2/nzbget-sab-bridge" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DATA_DIR=/data \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=6789

RUN groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid bridge --no-create-home --shell /usr/sbin/nologin bridge \
    && install -d -o bridge -g bridge -m 0750 /data

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt

COPY --chown=10001:10001 src /app/src
COPY --chown=10001:10001 LICENSE THIRD_PARTY_NOTICES.md /app/
COPY --chown=10001:10001 licenses /app/licenses

USER 10001:10001
EXPOSE 6789
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "nzbget_sab_bridge.healthcheck"]

CMD ["python", "-m", "nzbget_sab_bridge"]
