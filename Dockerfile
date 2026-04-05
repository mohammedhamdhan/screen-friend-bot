# Stage 1: build dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2: runtime image
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app ./app
COPY bot ./bot
COPY alembic ./alembic
COPY alembic.ini .
COPY start-api.sh .
COPY start-worker.sh .

# Default: run the API (Railway overrides this per service)
CMD ["./start-api.sh"]
