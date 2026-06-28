# --- Build Stage ---
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Final Production Stage ---
FROM python:3.11-slim AS runner

WORKDIR /app

# Add path to pip user directory
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy installed libraries from builder stage
COPY --from=builder /root/.local /root/.local

# Copy application source code
COPY app/ ./app

# Expose default API port
EXPOSE 8000

# Start command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
