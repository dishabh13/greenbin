# Use lightweight base image
FROM python:3.11-slim-bookworm

ARG REPO_URL
LABEL org.opencontainers.image.source=$REPO_URL

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps (needed for healthcheck + sqlite safety)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Create app directory structure (IMPORTANT for SQLite)
RUN mkdir -p /app && chmod -R 777 /app

# Install dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Create non-root user
RUN useradd -m appuser

# Give ownership to appuser (important for DB write)
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 5000

ENV FLASK_APP=app.py

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail http://localhost:5000/ || exit 1

# Run app
CMD ["sh", "-c", "python -c 'from app import app, init_db; app.app_context().push(); init_db()' && gunicorn -b 0.0.0.0:5000 app:app"]