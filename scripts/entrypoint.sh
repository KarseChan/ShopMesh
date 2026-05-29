#!/bin/sh
set -e

echo "Running database migrations..."
python scripts/migrate.py

echo "Starting application..."
exec uvicorn src.api.chat:app --host 0.0.0.0 --port 8000
