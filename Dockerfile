FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]