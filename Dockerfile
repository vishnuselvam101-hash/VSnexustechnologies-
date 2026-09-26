FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8000
# The service remains loopback-bound unless an operator intentionally overrides this command.
CMD ["uvicorn", "vnxdna.api.app:app", "--host", "127.0.0.1", "--port", "8000"]
