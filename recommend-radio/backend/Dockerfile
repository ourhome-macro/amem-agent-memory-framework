FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY recommend-radio/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY recommend-radio/backend .

EXPOSE 5000 9090

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
