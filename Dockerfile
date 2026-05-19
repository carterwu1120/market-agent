FROM python:3.11-slim

WORKDIR /app

# System deps for lxml, psycopg, hiredis
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Copy everything first so editable install can find src/
COPY . .

# Install in editable mode; fall back to non-dev if dev extras are absent
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "src.main"]
