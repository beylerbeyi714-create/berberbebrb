FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg espeak-ng \
      fonts-dejavu-core ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN --mount=type=secret,id=extra_ca,required=false \
    python3 -m venv /opt/venv && \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN userdel -r ubuntu 2>/dev/null; useradd -u 1000 -m factory
USER factory
ENV PATH=/opt/venv/bin:$PATH MEDIA_ROOT=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
