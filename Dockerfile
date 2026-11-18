FROM ghcr.io/astral-sh/uv:0.9.17 AS uv
FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PYTHONDONTWRITEBYTECODE=1 HOME=/tmp PYTHONPATH=/function
WORKDIR /function
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && uv cache clean
COPY extractor ./extractor
COPY func.py .
ENTRYPOINT ["/opt/venv/bin/fdk","/function/func.py","handler"]
