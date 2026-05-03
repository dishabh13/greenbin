FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy + install Python deps FIRST (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create user BEFORE copying app code
RUN useradd -m appuser

# Copy app code
COPY . .

# Fix permissions BEFORE switching user
RUN chown -R appuser:appuser /app && chmod +x start.sh

# Switch to non-root user
USER appuser

EXPOSE 5000

CMD ["./start.sh"]