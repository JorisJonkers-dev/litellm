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

## The model catalogue and explicit models

`catalog/sync.py` runs hourly (minute 17) as the `litellm-catalog` scheduled Process. It lists what each route's provider offers, probes what the account can use, registers the usable models in the proxy, and publishes the result.

- **Listing and probing.** Each route's provider list is read with the route's own key. A listed model is not necessarily usable (a Go model hosted outside the allowed region is listed and refused), so a new, errored or day-old model gets one minimal chat request (`max_tokens` 16; a provider that names a higher floor is retried once at it) sent to the provider itself with the route's key, and is classified `usable`, `blocked` (the provider's reason kept), `unknown`, `budget-exhausted`, `error`, `not-chat`, `unprobed` or `pending`. The probe goes to the provider, not through the proxy, because a model the proxy has not registered yet has no route to go through.
- **Explicit models.** A usable model is registered in the proxy database through the admin API (`/model/new`, `store_model_in_db: true`) as `<route>/<provider model id>`, with `litellm_params` naming the route's key and base as `os.environ/<NAME>` (the secret never leaves the proxy's environment), `model_info.access_groups: [<route>]` and `model_info.managed_by: litellm-catalog`. They appear on the admin UI's Models page. The sync touches only models carrying that marker, never the config's own. It removes a model once its provider has not listed it on two consecutive runs, or two consecutive probes found it `blocked` or `unknown`; `error`, `budget-exhausted`, `pending` and `unprobed` never remove one (no flapping). A run adds at most 100 models and removes at most 25 (a larger removal is refused and the run fails), and a route whose list failed keeps everything it had.
- **Keys.** A virtual key restricted to `vibe/*` matches the explicit `vibe/<id>` names by pattern, so keys are unchanged. A key can instead list the access group `vibe`.
- **Wildcards.** The wildcard routes stay in `config/config.yaml` as the rollout fallback (an exact name wins over a wildcard). Remove a route's wildcard only after the overview below shows its Registered count equal to its Usable count. Rollback of the explicit models is deleting the models whose `model_info.managed_by` is `litellm-catalog` from the Models page (or stopping the Process); the wildcards then serve every name as before.
- **The overview.** The same run publishes `README.md` beside `model-catalog.json` in the private `JorisJonkers-dev/litellm-model-catalog`: per route, counts, the usable models (free flag, context length, last probed, registered) and the unavailable ones with the provider's reason, committed only when the content changes. The `local` route lists nothing while the GPU workstation is unreachable from the cluster; the overview says so.

Published: ids, classification, reason text, free flag, context length, registration and probe times; never a key, a prompt or a response body. The format of `model-catalog.json` follows the Hermes model-catalog schema that `JorisJonkers-dev/openrouter-model-catalog` generates (`version`, `providers.<route>.models[]` with `id`, `description: "free"` and `metadata`). `python3 catalog/sync.py` writes both files locally instead when `CATALOG_REPO` and `CATALOG_TOKEN` are not set.

Probe cost bound: each probe is about 8 input and at most 16 output tokens (doubled only for a provider that demands a higher minimum), once per model per day, at most 250 a run and 30 free OpenRouter models a run; free and local models cost nothing. A Grafana panel for registered versus usable counts per route can be built from the catalogue's `by_status` and `registered_count`; none ships here.

## Deployment

`deploy/litellm.project.yml` is the deploy-kit Project Intent: Project `litellm` in `litellm-system` at `litellm.jorisjonkers.dev`, with its environment in `deploy/env/litellm/base.env` and its secrets at `secret/data/platform/litellm` (`master_key`, `salt_key`, `vibe_api_key`, `vibe_study_api_key`, `opencode_go_api_key`, `openrouter_api_key`, `catalog_publish_token`). CI validates both with the deploy-kit release `tools/deploy-kit` pins:

```bash
cd tools/deploy-kit && NODE_AUTH_TOKEN="$(gh auth token)" npm ci --ignore-scripts
npx --no-install deploy-kit validate ../../deploy/litellm.project.yml ../../deploy/env ../../deploy/dashboards
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
