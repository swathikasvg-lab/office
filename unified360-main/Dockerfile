FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip wheel && \
    pip install -r /app/requirements.txt && \
    pip install gunicorn

COPY . /app

RUN mkdir -p /app/data && \
    chmod +x /app/docker/entrypoint-web.sh /app/docker/entrypoint-alert.sh

EXPOSE 5050

ENTRYPOINT ["/app/docker/entrypoint-web.sh"]
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5050", "app:app"]
