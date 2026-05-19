FROM python:3.11-slim

WORKDIR /app

# System deps for lxml, psycopg, hiredis
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "src.main"]
