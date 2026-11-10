# litellm

LiteLLM for the JorisJonkers-dev estate: `ghcr.io/jorisjonkers-dev/litellm`, the one proxy the estate's cheap agents reach their models through. Each agent id gets its own virtual key, restricted to its models and capped with a hard budget (agent-kit#454).

## The image

- `Dockerfile` pins the LiteLLM base by tag and digest. Renovate bumps both.
- `config/config.yaml` is the model list, copied into the image so no deployment mounts configuration. It names environment variables for every upstream key and holds no value. There is no wildcard, no OpenRouter and no Zen route.

## Deployment

`deploy/litellm.project.yml` is the deploy-kit Project Intent: Project `litellm` in `litellm-system` at `litellm.jorisjonkers.dev`, with its environment in `deploy/env/litellm/base.env` and its secrets at `secret/data/platform/litellm` (`master_key`, `salt_key`, `vibe_api_key`, `vibe_study_api_key`, `opencode_go_api_key`). CI validates both with the deploy-kit release `tools/deploy-kit` pins:

```bash
cd tools/deploy-kit && NODE_AUTH_TOKEN="$(gh auth token)" npm ci --ignore-scripts
npx --no-install deploy-kit validate ../../deploy/litellm.project.yml ../../deploy/env
```

`salt_key` encrypts the upstream keys stored in the database. Never change it once virtual keys exist.

## Tests

```bash
npm test                                          # config checks, then builds the image and starts it
IMAGE=ghcr.io/jorisjonkers-dev/litellm@sha256:<digest> npm test   # tests an existing image
```

The image tests need Docker. They start the proxy read-only with only the project file's `writablePaths` writable, and check that it serves exactly the configured models to the master key alone.

## Releases

release-please keeps a release PR from conventional commits. Merging it tags `vX.Y.Z`; the tag publishes a multi-arch image, reads it back by digest, runs the image tests against it, and publishes the Intent Fragment the estate composes. The LiteLLM version the image ships is recorded as the images lock's `version`.
