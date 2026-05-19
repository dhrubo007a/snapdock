# Pull the Docker CLI and Compose plugin from the official image
FROM docker:27-cli AS docker-cli

FROM python:3.11-slim

# Install tar (needed for volume copy via Alpine containers invoked by the daemon)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Copy docker CLI and compose plugin so the daemon can run `docker compose up -d`
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY snapdock/ ./snapdock/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Persistent snapshot storage (DB lives in PostgreSQL now)
VOLUME ["/var/lib/snapdock/snapshots"]

EXPOSE 8000

CMD ["./entrypoint.sh"]
