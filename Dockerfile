FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV GEW_DB_PATH=/data/gew.db GEW_HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
