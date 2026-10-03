# Obscura Gateway container. It starts browser containers through the host's
# Docker socket, so run it with network_mode: host (see docker-compose.yml).
FROM docker:27-cli
RUN apk add --no-cache python3 bash coreutils
WORKDIR /app
COPY gateway.py obscura-gw client-setup.sh ./
RUN chmod 755 gateway.py obscura-gw && ln -s /app/obscura-gw /usr/local/bin/obscura-gw \
 && mkdir -p /config /data/crashes
ENV OBSCURA_GW_ENV_FILE=/config/gateway.env \
    OBSCURA_GW_CRASH_DIR=/data/crashes \
    PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD ["python3", "/app/gateway.py", "--healthcheck"]
ENTRYPOINT ["python3", "/app/gateway.py"]
