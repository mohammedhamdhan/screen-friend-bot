#!/bin/sh
set -e

echo "Starting Celery worker with beat scheduler..."
exec celery -A app.workers.celery_app worker --beat --loglevel=info
