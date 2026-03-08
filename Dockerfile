FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for caching
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --no-dev --no-install-project

# Copy source code
COPY src/ src/
COPY config.example.yaml ./

# Create data and logs directories
RUN mkdir -p data logs

EXPOSE 8000

CMD ["uv", "run", "order-guard", "serve", "--host", "0.0.0.0", "--port", "8000"]
