FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/uploads/products static/uploads/avatars static/uploads/covers

EXPOSE 8000

CMD sh -c "mkdir -p static/uploads/products static/uploads/avatars static/uploads/covers && exec gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000"
