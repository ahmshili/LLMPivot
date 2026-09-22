FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY config.example.yaml ./config.example.yaml

ENV AI_GATEWAY_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uv", "run", "ai-gateway", "--host", "0.0.0.0", "--port", "8000"]
