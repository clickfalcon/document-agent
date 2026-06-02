FROM python:3.11-slim

# 1. Install system dependencies (Tesseract, OpenCV/GLib, and build tools for pip)
# Build tools (gcc, python3-dev) are required if any python packages need to compile C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Setup non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# 3. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy source code and set permissions
COPY . .
RUN chown -R appuser:appuser /app

# 5. Switch to non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]