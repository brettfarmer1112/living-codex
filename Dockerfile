FROM python:3.10-slim

WORKDIR /app

# Install deps first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source and scripts
COPY src/ src/
COPY scripts/ scripts/

# Create data and inputs directories
RUN mkdir -p /app/data /app/inputs

# Lowest CPU + I/O priority to yield to AVAX validator
CMD ["nice", "-n", "19", "ionice", "-c", "3", "python", "-m", "living_codex.main"]
