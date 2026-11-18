FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    CONRADO_BACKEND=postgres \
    CONRADO_BIND=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system conrado && useradd --system --gid conrado --home-dir /app conrado

COPY requirements-postgres.txt ./
RUN pip install --no-cache-dir --requirement requirements-postgres.txt

COPY conrado ./conrado
COPY web ./web
COPY infra ./infra

USER conrado
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/readyz',timeout=3).read()"]

CMD ["python", "-m", "conrado.gestao"]
