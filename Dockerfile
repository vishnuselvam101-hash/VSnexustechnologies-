# CPU-only image with the vnx-dna CLI. Example:
#   docker build -t vnx-dna . && docker run --rm -v "$PWD:/work" -w /work vnx-dna store input.bin -o input.vxdna
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home vnx
USER vnx
ENTRYPOINT ["vnx-dna"]
CMD ["--help"]
