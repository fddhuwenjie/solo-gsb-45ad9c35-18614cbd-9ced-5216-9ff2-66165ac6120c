FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/theater.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY templates ./templates
COPY static ./static

# 数据库所在目录：由 compose 挂载命名卷，容器重启后保留工作稿与发布版
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 先幂等初始化/校验数据库，再启动。多 worker 下靠 SQLite BEGIN IMMEDIATE 串行写入
CMD ["sh", "-c", "flask --app app init-db && exec gunicorn --workers 2 --threads 4 --worker-class gthread --bind 0.0.0.0:8000 --timeout 60 app:app"]
