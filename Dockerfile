FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    MULTICONTROL_DATA_DIR=/app/data \
    MULTICONTROL_PORT=8765 \
    MULTICONTROL_UDP_PORT=8766 \
    MULTICONTROL_LAN_IP="" \
    MULTICONTROL_TAILSCALE_IP=""

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m -s /bin/sh appuser && \
    mkdir -p /app/data && \
    chown -R 1000:1000 /app

COPY server /app/server
COPY client /app/client

USER 1000:1000

EXPOSE 8765/tcp
EXPOSE 8766/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)" || exit 1

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8765"]
