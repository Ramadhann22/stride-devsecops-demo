FROM python:3.12-slim

ENV DEBUG_MODE=true
ENV APP_VERSION=1.0-compromised

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
