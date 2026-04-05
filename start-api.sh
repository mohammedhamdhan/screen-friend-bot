#!/bin/sh
set -e

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start the API server on Railway's PORT
echo "Starting API server on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
