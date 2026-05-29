FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2-binary and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make entrypoint executable
RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["sh", "scripts/entrypoint.sh"]
