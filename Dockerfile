# LiteLLM with the estate's model routes built in, so no deployment mounts configuration, and
# the catalogue sync (catalog/sync.py), which the scheduled catalog Process runs from this
# same image. Digest of v1.109.1 read from ghcr.io on 2026-11-17; Renovate bumps tag and digest.
# 1.109 is the first line with LiteLLM's own opencode_go provider (BerriAI/litellm#48140), which
# config.yaml's OpenCode Go route needs: 1.108.x does not have it.
# The tag names the LiteLLM version, and the shared image build stamps it as the image's
# org.opencontainers.image.version label, which the images lock records. A floating tag such as
# main-stable names no version, so the lock refuses the image.
# The non-root variant of the LiteLLM release: its app and migration paths are owned by its user
# (nobody), and HOME is /app.
FROM ghcr.io/berriai/litellm-non_root:v1.109.1@sha256:e9e15fe17b66fc8cda6ff7e18914e1d452785ca404fa20336ec447152f86daba

COPY config/config.yaml /app/config.yaml
# The config's `include` resolves next to it, so the generated deployments land in /app too.
COPY config/models.generated.yaml /app/models.generated.yaml
# The telemetry callback config.yaml's `callbacks` names (module.instance, next to the config).
COPY config/tier_telemetry.py /app/tier_telemetry.py
# The account spending caps config.yaml's `callbacks` names (read from catalog/routing.json, copied below).
COPY config/account_budget.py /app/account_budget.py
COPY catalog/sync.py catalog/overview.py catalog/routing.json /app/catalog/

# Numeric, so the images lock can record the uid and gid (nobody). The process's writablePaths
# (/tmp and /app/.cache, which is HOME's cache) are the only places it writes at runtime.
USER 65534:65534

# Metrics are served by their own process on 4001 (no virtual-key auth, so the Service for it
# is reachable only by the monitoring stack, which the scrape declaration admits).
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0", "--prometheus_metrics_port", "4001"]
