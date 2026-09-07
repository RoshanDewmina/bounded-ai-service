FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /bin/
COPY . .
RUN uv sync --frozen --no-dev
ENV HOST=0.0.0.0 PORT=8112
EXPOSE 8112
CMD ["uv", "run", "--no-sync", "python3", "-m", "bounded_ai.api"]
