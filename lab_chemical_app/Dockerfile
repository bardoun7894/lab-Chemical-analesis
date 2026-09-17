FROM python:3.12-slim

WORKDIR /app

# Install system dependencies.
#
# fonts-dejavu-core supplies /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
# and DejaVuSans-Bold.ttf — the first entries in the sticker renderer's
# FONT_PATHS lists. Without it python:3.12-slim carries no TrueType fonts at
# all, _load_font() falls back to PIL's tiny bitmap default, and the pipe
# label renders in the sparse pre-dab2e39 style even though the improved
# layout is running.
#
# postgresql-client supplies pg_dump, which backup_service uses to snapshot the
# database when DATABASE_URL points at Postgres; without it the Backups screen
# raises on every attempt. Debian trixie's own package is client 17, and pg_dump
# reads any server at or below its own major version, so it covers the
# Postgres 16 server in docker-compose.yml. If that server is ever upgraded
# past 17, this client has to move first — pg_dump refuses a newer server.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    fonts-dejavu-core \
    libdmtx0b \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the port
EXPOSE 8000

# Set environment variables
ENV FLASK_APP=app
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Run the application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:create_app()", "--workers", "4", "--timeout", "120"]
