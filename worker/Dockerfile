FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
COPY requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir -r backend/requirements.txt && useradd --uid 10001 --create-home queueflow
COPY backend /app/backend
COPY shared /app/shared
COPY worker /app/worker
COPY scripts /app/scripts
USER queueflow
CMD ["python", "-m", "worker.main"]
