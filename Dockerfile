# Python base remains digest-pinned; Rust builder stays version-pinned because the
# official worker transplant requires the Polymarket SDK's current Rust toolchain.
FROM rust:1.88.0-slim-bookworm AS worker-builder

WORKDIR /build/workers

COPY workers/Cargo.toml /build/workers/Cargo.toml
COPY workers/Cargo.lock /build/workers/Cargo.lock
COPY workers/src /build/workers/src

RUN cargo build --release --locked

FROM python:3.11-slim@sha256:d6e4d224f70f9e0172a06a3a2eba2f768eb146811a349278b38fff3a36463b47 AS python-deps-builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /build/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /build/python/requirements.txt

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheelhouse -r /build/python/requirements.txt

FROM python:3.11-slim@sha256:d6e4d224f70f9e0172a06a3a2eba2f768eb146811a349278b38fff3a36463b47

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY --from=python-deps-builder /wheelhouse /wheelhouse
RUN pip install --no-cache-dir /wheelhouse/* \
    && rm -rf /wheelhouse

COPY . /app
COPY --from=worker-builder /build/workers/target/release/bro-market-stream-worker /app/workers/bin/bro-market-stream-worker
COPY --from=worker-builder /build/workers/target/release/bro-rtds-stream-worker /app/workers/bin/bro-rtds-stream-worker

RUN addgroup --system bro && adduser --system --ingroup bro --home /home/bro --shell /usr/sbin/nologin bro \
    && mkdir -p /data /logs /tmp /config \
    && chown -R bro:bro /app /data /logs /tmp /config

USER bro

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python scripts/container_healthcheck.py --config /config/profiles/paper_universal.yaml

CMD ["python", "executor.py", "--config", "execution_config.yaml", "--mode", "paper"]
