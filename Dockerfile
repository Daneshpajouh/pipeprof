# Stages: build (wheel), test (runs the suite on Linux), runtime (default, CLI only).
#   docker build -t hjp .                      # runtime image
#   docker build --target test -t hjp-test . && docker run --rm hjp-test
ARG PYTHON_IMAGE=python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build==1.3.0 && python -m build --wheel --outdir /dist

FROM ${PYTHON_IMAGE} AS test
RUN apt-get update \
    && apt-get install -y --no-install-recommends bcftools samtools tabix git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir -e ".[dev]"
CMD ["pytest", "-q", "-p", "no:cacheprovider", "--cov=hpc_job_profiler", "--cov-report=term"]

FROM ${PYTHON_IMAGE} AS runtime
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home hjp
USER hjp
WORKDIR /home/hjp
ENTRYPOINT ["hjp"]
CMD ["--help"]
