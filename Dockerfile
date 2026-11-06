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
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib
# Tesseract with the Kazakh, Russian and English language packs for the OCR analyzer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr tesseract-ocr-kaz tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist/*.whl /tmp/
# Optional analyzers to install. The CV analyzer (PyTorch) is opt-in:
#   docker build --build-arg EXTRAS=integrity,ocr,geo,cv .
# and mount the YOLO weights at run time (-e AFA_YOLO_WEIGHTS=/models/yolov8x.pt).
ARG EXTRAS=integrity,ocr,geo
RUN case ",${EXTRAS}," in \
      *,cv,*) index="--extra-index-url https://download.pytorch.org/whl/cpu" ;; \
      *) index="" ;; \
    esac \
    && pip install $index "$(ls /tmp/*.whl)[${EXTRAS}]" && rm /tmp/*.whl \
    # ultralytics pulls opencv-python, which needs libGL; keep the headless build only.
    && if pip show opencv-python >/dev/null 2>&1; then \
         pip uninstall -y opencv-python \
         && pip install --force-reinstall --no-deps "opencv-python-headless>=4.8,<5"; \
       fi \
    && useradd --create-home --uid 10001 afa
USER afa
WORKDIR /home/afa
ENTRYPOINT ["afa"]
CMD ["--help"]
