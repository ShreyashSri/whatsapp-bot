FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

USER nobody
EXPOSE 3000
CMD ["uvicorn", "pbbot.api:app", "--host", "0.0.0.0", "--port", "3000"]
