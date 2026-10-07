# CPU-only image with the vnx-dna CLI. Example (run as your own user so the mounted directory is writable):
#   docker build -t vnx-dna . && docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work vnx-dna store input.bin -o input.vxdna
# Native kernels (aligner, read parser, RS decoder, read clustering) are compiled in the build stage; the runtime image has no compiler.
# Check: docker run --rm --entrypoint python vnx-dna -m vnxdna.native
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml setup.py README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --no-deps -w /wheels .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /wheels /wheels
# --require-native: the image build fails if a kernel did not compile, instead of shipping the slower reference paths
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels && python -m vnxdna.native --require-native > /dev/null \
    && useradd --create-home vnx
USER vnx
ENTRYPOINT ["vnx-dna"]
CMD ["--help"]
