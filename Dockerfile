FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LP_DEFAULT_CONFIG=/app/defaults/config.yaml \
    LP_CONFIG=/app/config/config.yaml \
    LP_DATA_DIR=/app/data \
    TZ=Europe/Paris

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY CHANGELOG.md ./CHANGELOG.md
# Config par défaut : copiée dans /app/config au 1er démarrage si absente
COPY config/config.yaml ./defaults/config.yaml

VOLUME ["/app/config", "/app/data"]

CMD ["python", "-m", "bot"]
