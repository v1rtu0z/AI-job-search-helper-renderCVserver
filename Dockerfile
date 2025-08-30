FROM python:3.12-slim as base

# Build stage
FROM base as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM base
WORKDIR /app

RUN apt-get update && \
    apt-get install -y \
    texlive-latex-base \
    texlive-fonts-recommended \
    pandoc \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Copy from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "120", "--workers", "2", "--log-level", "info", "--access-logfile", "-", "--error-logfile", "-", "app:app"]