# LiteLLM with the estate's model routes built in, so no deployment mounts configuration, and
# the catalogue sync (catalog/sync.py), which the scheduled catalog Process runs from this
# same image. Digest of v1.108.0 read from ghcr.io on 2026-11-10; Renovate bumps tag and digest.
# The tag names the LiteLLM version, and the shared image build stamps it as the image's
# org.opencontainers.image.version label, which the images lock records. A floating tag such as
# main-stable names no version, so the lock refuses the image.
# The non-root variant of the LiteLLM release: its app and migration paths are owned by its user
# (nobody), and HOME is /app.
FROM ghcr.io/berriai/litellm-non_root:v1.108.0@sha256:08d313a00c46509c875d84f60c1448a260c478f399ade3c2abb940d358d5b724

COPY config/config.yaml /app/config.yaml
# The config's `include` resolves next to it, so the generated deployments land in /app too.
COPY config/models.generated.yaml /app/models.generated.yaml
COPY catalog/sync.py catalog/overview.py catalog/routing.json /app/catalog/

# Numeric, so the images lock can record the uid and gid (nobody). The process's writablePaths
# (/tmp and /app/.cache, which is HOME's cache) are the only places it writes at runtime.
USER 65534:65534

# Metrics are served by their own process on 4001 (no virtual-key auth, so the Service for it
# is reachable only by the monitoring stack, which the scrape declaration admits).
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0", "--prometheus_metrics_port", "4001"]
