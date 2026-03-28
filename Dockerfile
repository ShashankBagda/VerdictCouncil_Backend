# Stage 1: Builder
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /install /usr/local
COPY src/ /app/src/
COPY configs/ /app/configs/
RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"
ENTRYPOINT ["python", "-m", "solace_agent_mesh.main"]
CMD ["--config", "/app/configs/agents/case-processing.yaml"]
