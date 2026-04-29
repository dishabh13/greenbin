# Use lightweight base image
FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/diyajohn2024/greenybin"

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps (needed for healthcheck)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd -m appuser

# Copy only necessary files
COPY . .

# Switch to non-root user
USER appuser

EXPOSE 5000

ENV FLASK_APP=app.py

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail http://localhost:5000/ || exit 1

# Run app
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]