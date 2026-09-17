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
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    fonts-dejavu-core \
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
