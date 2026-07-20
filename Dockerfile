FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/personal_finance
WORKDIR /app

EXPOSE 8000
CMD ["sh", "-c", "alembic -c personal_finance/alembic.ini upgrade head && uvicorn personal_finance.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
