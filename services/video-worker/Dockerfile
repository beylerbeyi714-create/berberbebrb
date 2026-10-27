FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg \
      fonts-dejavu-core fontconfig ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# extra_ca: yalnızca TLS denetimli ağlarda (kurumsal proxy / sandbox) gerekir; yoksa yok sayılır
RUN --mount=type=secret,id=extra_ca,required=false \
    python3 -m venv /opt/venv && \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY worker.py .
RUN userdel -r ubuntu 2>/dev/null; useradd -u 1000 -m worker
USER worker
ENV PATH=/opt/venv/bin:$PATH MEDIA_ROOT=/data
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/health')"
CMD ["sh", "-c", "mkdir -p $MEDIA_ROOT/tmp && exec uvicorn worker:app --host 0.0.0.0 --port 8100"]
