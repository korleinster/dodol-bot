FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /build
RUN python -m venv "${VIRTUAL_ENV}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import davey, discord, nacl; assert discord.__version__ == '2.7.1'" \
    && python -m pip uninstall --yes pip setuptools

FROM python:3.11-slim AS runtime

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY main.py .
COPY src ./src
RUN python -c "from src.cogs.tts import detect_voice_runtime_capability; assert detect_voice_runtime_capability() == (True, 'VOICE_RUNTIME_READY')"

# 빌드 시 git 커밋 해시 주입 (docker compose build --build-arg GIT_COMMIT=... 또는 자동 전달)
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

CMD ["python", "main.py"]
