FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# 状态在单进程内存中，必须单 worker（多线程处理并发轮询）
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "16", "app:app"]
