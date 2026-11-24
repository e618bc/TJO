FROM python:3.12-slim
WORKDIR /app
COPY vacancy_clarity ./vacancy_clarity
COPY config.example.ini ./config.ini
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "vacancy_clarity", "schedule"]
