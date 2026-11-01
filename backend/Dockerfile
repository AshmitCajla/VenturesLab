FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 HF_HOME=/app/.cache FASTEMBED_CACHE_PATH=/app/.cache/fastembed

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Bake the embedding model into the image so cold starts do not download it
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && useradd -m app && mkdir -p /app/data/logs && chown -R app /app
# /app/data holds the SQLite database and JSON logs; mount a volume there to keep runs across restarts
USER app

# Render/Railway inject $PORT; Hugging Face Spaces expects 7860
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
