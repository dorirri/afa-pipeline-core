# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build==1.6.1
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="afa-pipeline-core" \
      org.opencontainers.image.description="AFA-Pipeline core: read-only ingestion, EXIF anomaly analysis and hash-chained audit log" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/dorirri/afa-pipeline-core"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home --uid 10001 afa
USER afa
WORKDIR /home/afa
ENTRYPOINT ["afa"]
CMD ["--help"]
