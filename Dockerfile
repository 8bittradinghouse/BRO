# Pinned base image digest for reproducible builds.
FROM python:3.11-slim@sha256:d6e4d224f70f9e0172a06a3a2eba2f768eb146811a349278b38fff3a36463b47

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN addgroup --system bro && adduser --system --ingroup bro --home /home/bro --shell /usr/sbin/nologin bro \
    && mkdir -p /data /logs /tmp /config \
    && chown -R bro:bro /app /data /logs /tmp /config

USER bro

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python scripts/container_healthcheck.py --config /config/profiles/paper_universal.yaml

CMD ["python", "executor.py", "--config", "execution_config.yaml", "--mode", "paper"]
