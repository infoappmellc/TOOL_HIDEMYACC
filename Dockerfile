FROM python:3.12-slim

WORKDIR /app
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt
COPY apps ./apps
RUN mkdir -p /app/data

ENV DATABASE_PATH=/app/data/autopost.db
EXPOSE 8000
CMD ["python", "-m", "apps.dashboard"]

