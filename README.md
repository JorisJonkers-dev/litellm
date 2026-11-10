# litellm

LiteLLM for the JorisJonkers-dev estate: `ghcr.io/jorisjonkers-dev/litellm`, the one proxy the estate's cheap agents reach their models through. Each agent id gets its own virtual key, restricted to its models and capped with a hard budget (agent-kit#454).

## The image

- `Dockerfile` pins the LiteLLM base by tag and digest. Renovate bumps both.
- `config/config.yaml` is the route list, copied into the image so no deployment mounts configuration. Every route is a provider wildcard, so any model a provider offers works the day it appears: a caller sends `<route>/<provider model id>`.

| Route | Provider | Upstream key (Vault field) | Budget (agent-kit registry) |
|---|---|---|---|
| `vibe/*` | Mistral | `vibe_api_key` | 255 |
| `vibe-study/*` | Mistral | `vibe_study_api_key` | 128 |
| `opencode-go/*` | OpenCode Go | `opencode_go_api_key` | 60 |
| `openrouter/*` | OpenRouter | `openrouter_api_key` | 5; use the `:free` ids |
| `local/*` | OpenAI-compatible server on the GPU workstation | none | none (free) |

The config names environment variables for every key and base and holds no value. There is no Zen route.

## Telemetry

Metadata only: model, key alias, tokens, cost, latency, status. `turn_off_message_logging: true` blanks prompts and responses in every callback, and the image test sends a marked prompt and asserts the marker is absent from the logs, the exported traces and the metrics (and that traces were exported, so the absence means something).

- **Metrics.** LiteLLM's `prometheus` callback is open source in the pinned release (1.108.0): no premium-user check anywhere on its path. It serves `/metrics/` from its own process on port 4001 (`--prometheus_metrics_port`), declared as the Application's `observability.scrape`, with only the labels `api_key_alias`, `requested_model`, `status_code` and `exception_status`.
- **Traces.** The `otel` callback exports spans to the collector the `python` runtime profile names (`OTEL_EXPORTER_OTLP_ENDPOINT`, gRPC).
- **Dashboard.** `deploy/dashboards/litellm.json` (spend, tokens, latency, errors and budget per key and model) ships with the Project. The alert rules live with the monitoring stack, in estate `platform/observability/rules/litellm.yaml`.
- **A price caveat.** A budget only counts what LiteLLM can price. A model it has no price for records spend 0, so a cap does not bite on it; the alert `LiteLLMSpendBlind` fires when a paid key moves tokens and no spend.

## The model catalogue

`catalog/sync.py` lists what each route's provider offers and probes what the account can use: a provider's list is not what an account may call (a listed Go model hosted outside the allowed region is refused). One 1-token request per model goes through the proxy, so the route's own upstream key is the one tested, and the answer is classified `usable`, `blocked` (the provider's reason text kept), `unknown`, `budget-exhausted`, `error`, `not-chat` or `unprobed`. The catalogue marks free models and caches each probe with a timestamp: a run probes only models that are new, errored or older than 24 hours, and a model that leaves a provider's list leaves the catalogue. Paid OpenRouter models are not probed, and only 30 free OpenRouter models are probed a run (the free tier allows few requests a day).

The format follows the Hermes model-catalog schema that `JorisJonkers-dev/openrouter-model-catalog` generates (`version`, `providers.<route>.models[]` with `id`, `description: "free"` and `metadata`), so a picker reads it the same way. It runs as the hourly `litellm-catalog` scheduled Process from this same image and commits `model-catalog.json` to `JorisJonkers-dev/litellm-model-catalog` when anything changed. That repository should be private: the reasons name account settings. `python3 catalog/sync.py` writes the file locally instead when `CATALOG_REPO` and `CATALOG_TOKEN` are not set.

Probe cost bound: models probed x (about 8 input tokens + 1 output token) once per model per day, at most 250 a run. With the 79 Mistral models listed per key, the OpenCode Go list and 30 free OpenRouter ids that is under 400 requests a day, a few thousand tokens in all, nothing on free and local models.

## Deployment

`deploy/litellm.project.yml` is the deploy-kit Project Intent: Project `litellm` in `litellm-system` at `litellm.jorisjonkers.dev`, with its environment in `deploy/env/litellm/base.env` and its secrets at `secret/data/platform/litellm` (`master_key`, `salt_key`, `vibe_api_key`, `vibe_study_api_key`, `opencode_go_api_key`, `openrouter_api_key`, `catalog_publish_token`). CI validates both with the deploy-kit release `tools/deploy-kit` pins:

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

The image tests need Docker. They start the proxy read-only with only the project file's `writablePaths` writable, and point every upstream at a stub: they check wildcard routing and per-route keys, the models list, the metrics and that no message text is logged. `npm run test:catalog` runs the catalogue sync's tests without Docker.

## Releases

release-please keeps a release PR from conventional commits. Merging it tags `vX.Y.Z`; the tag publishes a multi-arch image, reads it back by digest, runs the image tests against it, and publishes the Intent Fragment the estate composes. The LiteLLM version the image ships is recorded as the images lock's `version`.
