FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# numeric UID so Kubernetes can verify runAsNonRoot
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8080
# 1 worker + threads: the pod is limited to 250m CPU, so extra processes would not help
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "30", "--graceful-timeout", "20", "app:app"]
