FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir \
       "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.30,<1.0" \
       "python-multipart>=0.0.9" "pydantic>=2.8,<3.0" \
       "faster-whisper>=1.1,<2.0" "piper-tts>=1.3,<2.0"

COPY services/voice_gateway/ ./services/voice_gateway/
RUN mkdir -p /models && useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001
CMD ["uvicorn", "services.voice_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
