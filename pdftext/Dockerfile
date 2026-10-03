# Sandboxed PDF -> text helper for the obscura gateway.
# Run by the gateway with: --rm --read-only --tmpfs /tmp --network obscura-net --cap-drop ALL --user 65534
FROM debian:stable-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates poppler-utils \
 && rm -rf /var/lib/apt/lists/*
COPY pdftext.sh /usr/local/bin/pdftext
ENTRYPOINT ["/bin/sh", "/usr/local/bin/pdftext"]
