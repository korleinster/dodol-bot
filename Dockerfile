FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import davey, discord, nacl; assert discord.__version__ == '2.7.1'"

COPY . .

# 빌드 시 git 커밋 해시 주입 (docker compose build --build-arg GIT_COMMIT=... 또는 자동 전달)
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

CMD ["python", "-u", "main.py"]
