FROM python:3.11-slim
# rebuild: include static assets v2

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libev-dev \
    libffi-dev \
    libssl-dev \
    libpq-dev \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

ENV PORT=7860

CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "2", "--timeout", "120", "main:app"]
