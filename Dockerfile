# LiteLLM with the estate's model routes built in, so no deployment mounts configuration, and
# the catalogue sync (catalog/sync.py), which the scheduled catalog Process runs from this
# same image. Digest of main-stable read from ghcr.io on 2026-11-10; Renovate bumps tag and digest.
# The non-root variant of the same LiteLLM release (v1.108.0): its app and migration paths are
# owned by its user (nobody), and HOME is /app.
FROM ghcr.io/berriai/litellm-non_root:main-stable@sha256:08d313a00c46509c875d84f60c1448a260c478f399ade3c2abb940d358d5b724

COPY config/config.yaml /app/config.yaml
COPY catalog/sync.py /app/catalog/sync.py

# Numeric, so the images lock can record the uid and gid (nobody). The process's writablePaths
# (/tmp and /app/.cache, which is HOME's cache) are the only places it writes at runtime.
USER 65534:65534

# Metrics are served by their own process on 4001 (no virtual-key auth, so the Service for it
# is reachable only by the monitoring stack, which the scrape declaration admits).
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0", "--prometheus_metrics_port", "4001"]
