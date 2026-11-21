# Build from the repo root:  docker build -f backend/Dockerfile -t roadrec-api .
FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY python python
COPY backend backend
WORKDIR /srv/backend
ENV ENV=production
# set SECRET_KEY, INVITE_CODE, DATABASE_URL (Postgres recommended) and optionally NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
