FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.17
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY server ./server
COPY shared ./shared
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "server.api:from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000"]
