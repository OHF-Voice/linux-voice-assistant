FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mmwave_presence.py .

CMD ["python", "mmwave_presence.py"]
