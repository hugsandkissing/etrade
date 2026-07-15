FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SWAGGER_MODE=shadow \
    SWAGGER_BROKER_MODE=mock \
    HEALTH_HOST=0.0.0.0

WORKDIR /app

RUN groupadd --system swagger && useradd --system --gid swagger --home /app swagger

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY swagger ./swagger
RUN mkdir -p /app/swagger_state && chown -R swagger:swagger /app

USER swagger
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["python", "-m", "swagger.engine", "--health-port", "8080"]
