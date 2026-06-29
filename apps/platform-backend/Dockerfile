# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 GITOPS_WORKDIR=/tmp/gitops
# git is required for the GitOps deploy path (subprocess clone/commit/push).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder /install /usr/local
WORKDIR /app
COPY app ./app
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
