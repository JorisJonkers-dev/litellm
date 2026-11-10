# LiteLLM with the estate's model list built in, so no deployment mounts configuration.
# Digest of main-stable read from ghcr.io on 2026-11-10; Renovate bumps tag and digest.
FROM ghcr.io/berriai/litellm:main-stable@sha256:c560d317136880ea92d62334d4367ab10955b1af189dd3ea24f6551290b1d6c1

COPY config/config.yaml /app/config.yaml
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
# The base image runs as root today; a USER line, by number, is a follow-up once the image is
# confirmed to run unprivileged read-only (the images lock then records its uid and gid).
