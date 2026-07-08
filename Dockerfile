FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cloud Run injects PORT (defaults to 8080). Honor it via the shell so the
# container always listens on the right port; `exec` keeps uvicorn as PID 1 for
# clean signal handling. Shell form (not JSON) is required for ${PORT} expansion.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}