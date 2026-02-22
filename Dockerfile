FROM python:3.10-slim

WORKDIR /app

# Copy source and install
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Create data and inputs directories
RUN mkdir -p /app/data /app/inputs

# Lowest CPU + I/O priority to yield to AVAX validator
CMD ["nice", "-n", "19", "ionice", "-c", "3", "python", "-m", "living_codex.main"]
