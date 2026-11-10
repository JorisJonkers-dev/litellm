# LiteLLM with the estate's model routes built in, so no deployment mounts configuration, and
# the catalogue sync (catalog/sync.py), which the scheduled catalog Process runs from this
# same image. Digest of main-stable read from ghcr.io on 2026-11-10; Renovate bumps tag and digest.
FROM ghcr.io/berriai/litellm:main-stable@sha256:c560d317136880ea92d62334d4367ab10955b1af189dd3ea24f6551290b1d6c1

COPY config/config.yaml /app/config.yaml
COPY catalog/sync.py /app/catalog/sync.py
# Metrics are served by their own process on 4001 (no virtual-key auth, so the Service for it
# is reachable only by the monitoring stack, which the scrape declaration admits).
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0", "--prometheus_metrics_port", "4001"]
# The base image runs as root today; a USER line, by number, is a follow-up once the image is
# confirmed to run unprivileged read-only (the images lock then records its uid and gid).
