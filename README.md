# litellm

LiteLLM for the JorisJonkers-dev estate: `ghcr.io/jorisjonkers-dev/litellm`, the one proxy the estate's cheap agents reach their models through. Each agent id gets its own virtual key, restricted to its models and capped with a hard budget (agent-kit#454).

## The image

- `Dockerfile` pins the LiteLLM base by tag and digest. Renovate bumps both.
- `config/config.yaml` is the route list, copied into the image so no deployment mounts configuration. It holds three things: an `include` of `models.generated.yaml` (the deployments the catalogue sync generates), a `credential_list` with one entry per account, and a flat catch-all wildcard per account so an id the catalogue has not deployed yet still reaches its provider.
- `config/models.generated.yaml` is generated. It is a JSON document under a comment header, which is valid YAML, and it starts empty.

| Route (credential) | Provider | Upstream key (Vault field) | Plan |
|---|---|---|---|
| `vibe` | Mistral, Vibe plan | `vibe_api_key` | EUR 255 a month |
| `vibe-study` | Mistral, Vibe study plan | `vibe_study_api_key` | EUR 127.50 a month |
| `vibe-api` | Mistral, general API key | `vibe_api_general_key` | EUR 25.50 a month |
| `vibe-study-api` | Mistral, general API key | `vibe_study_api_general_key` | EUR 12.75 a month |
| `opencode-go` | OpenCode Go (OpenAI-compatible) | `opencode_go_api_key` | pay per token |
| `openrouter` | OpenRouter, free ids only, public work only | `openrouter_api_key` | free |
| `local` | OpenAI-compatible server on the GPU workstation, behind its keyed gate | `local_llm_api_key` | free |
| `google` | Google AI Studio, free tier | `google_api_key` | free tier |
| `anthropic` | Anthropic | `anthropic_api_key` | pay per token |

The four Mistral accounts are listed and probed separately because their model sets differ. A route whose key is unset or empty is skipped silently, so the sync runs before every key is written.

**Credentials.** `credential_list` holds one entry per account: `credential_name` (the route), `credential_values` (`api_key` and, except for Google and Anthropic, `api_base`) and the required `credential_info: {}`. Every value is an environment name (`os.environ/<NAME>`), never a value. A deployment names its entry with `litellm_params.litellm_credential_name`. LiteLLM sends **no credentials at all** when that name does not resolve, so `tests/config.test.mjs` fails on a dangling name in either config file: that check is the guard against a silently unauthenticated deployment. Google and Anthropic are addressed by LiteLLM itself, so their credentials carry the key only.

**Wildcards.** The catch-all wildcards are `vibe/*`, `vibe-study/*`, `vibe-api/*`, `vibe-study-api/*` (all `mistral/*`), `opencode-go/*` and `local/*` (both `openai/*`). There is no `openrouter/*` wildcard: OpenRouter is free only, and a suffix wildcard such as `openrouter/*:free` is not verified against the pinned release, so the generated deployments are the only way to reach an OpenRouter model. There is no Zen (`opencode.ai/zen/v1`) route: OpenCode's free models answer 403 to every client but OpenCode itself.

## Telemetry

Metadata only: model, key alias, tokens, cost, latency, status. `turn_off_message_logging: true` blanks prompts and responses in every callback, and the image test sends a marked prompt and asserts the marker is absent from the logs, the exported traces and the metrics (and that traces were exported, so the absence means something).

- **Metrics.** LiteLLM's `prometheus` callback is open source in the pinned release (1.108.0): no premium-user check anywhere on its path. It serves `/metrics/` from its own process on port 4001 (`--prometheus_metrics_port`), declared as the Application's `observability.scrape`. The request and error series carry `api_key_alias`, `requested_model`, `status_code` or `exception_status`, `model_id` and `api_provider`, so the deployment that served a request is visible per request.
- **Traces.** The `otel` callback exports spans to the collector the `python` runtime profile names (`OTEL_EXPORTER_OTLP_ENDPOINT`, gRPC).
- **Dashboard.** `deploy/dashboards/litellm.json` (spend, tokens, latency, errors and budget per key and model) ships with the Project. The alert rules live with the monitoring stack, in estate `platform/observability/rules/litellm.yaml`.
- **A price caveat.** A budget only counts what LiteLLM can price. A model it has no price for records spend 0, so a cap does not bite on it; the alert `LiteLLMSpendBlind` fires when a paid key moves tokens and no spend.

## The model catalogue and generated routing

`catalog/sync.py` runs hourly (minute 17) as the `litellm-catalog` scheduled Process. It lists what each account's provider offers, probes what the account can use, builds the routing plan, and opens a pull request with it. It never writes to the live proxy and needs no master key.

- **Listing and probing.** Each account's provider list is read with that account's own key. A listed model is not necessarily usable (a Go model hosted outside the allowed region is listed and refused), so a new, errored or day-old model gets one minimal chat request (`max_tokens` 16; a provider that names a higher floor is retried once at it) sent to the provider itself with the account's key, and is classified `usable`, `blocked` (the provider's reason kept), `unknown`, `budget-exhausted`, `error`, `not-chat`, `unprobed` or `pending`. The probe goes to the provider, not through the proxy.
- **Generated config, not a database.** The plan is written to `config/models.generated.yaml` in this repository and opened as a pull request from the `bot/model-catalog` branch. The proxy reads that file from the image, so a route changes only when the pull request is merged and the image is redeployed. The pull request is titled `fix(models): refresh the generated model deployments`, so merging it cuts a patch release (release-please does not release on `chore`). Nothing is stored in the proxy's database and the admin API is not used: `store_model_in_db` is gone.
- **One deployment per (canonical model, account).** Each carries `model_name` (the canonical group), `litellm_params` (`model` as `<prefix>/<upstream id>`, `litellm_credential_name`, `order`, `weight`, `max_budget` with `budget_duration`, `cooldown_time`) and `model_info` (`id` as `<route>/<upstream id>`, `route`, `canonical`, `mode` for a non-chat model, `access_groups`, and `health_check_model` where a wildcard names one). A model is dropped once its provider has not listed it on two consecutive runs, or two consecutive probes found it `blocked` or `unknown`; `error`, `budget-exhausted`, `pending` and `unprobed` never remove one (no flapping).
- **Modality.** `mode` is `embedding`, `ocr` or `audio_transcription`; a group never mixes modes, so a model whose mode differs from its name-mates gets the group name `<canonical>:<mode>`. Non-chat models come only from the general Mistral API keys (`api_only_routes`).
- **Exhaustion.** Vibe answers 402 (`billing_vibe_budget_exhausted`) when its pool is spent. LiteLLM maps 402 and 429 to `RateLimitError` and cools a deployment down for its `litellm_params.cooldown_time` (default 5 s), but **not** in a group of exactly one deployment unless `model_info.allowed_fails_policy` carries an explicit `RateLimitErrorAllowedFails` entry. So every deployment that has a cooldown in `catalog/routing.json` and sits alone in its group gets `{"RateLimitErrorAllowedFails": 0}`. That is why the cooldowns are long (an hour for the Mistral accounts): a spent plan steps aside for the next tier instead of failing the request.
- **Anthropic's 400.** Anthropic answers an exhausted limit with HTTP 400, a `BadRequest`. There is deliberately no `BadRequest` policy: cooling on every 400 would cool Claude on any malformed request. The 400 passes through to the caller with Anthropic's own text.
- **Google is a public free tier.** The AI Studio key is a free-tier key whose prompts Google may train on, so it is only for public work. Gemini deployments on `google` exist **only** in separate groups named `public/<canonical>`, with `model_info.access_groups: ["google-free-public"]`; the normal `<canonical>` group never contains `google`. A `public/<canonical>` group is the Google deployment at order 1 (no `max_budget`, `cooldown_time` 120) plus the same non-Google deployments the normal group has, renumbered after it. A virtual key without the `google-free-public` access group never sees `public/*`.
- **OpenRouter is free only.** Only ids whose metadata says free are ever deployed, with `extra_body {"provider": {"data_collection": "deny", "allow_fallbacks": false}}`. There is no paid OpenRouter fallback.
- **The free route is public work only.** A free-tier provider may train on the prompts, so `openrouter` deployments are in **no** `canonical` group and in no `public/*` group. Each one is alone in an `openrouter/<canonical>` group (`openrouter/glm-4.6`) with the access group `openrouter-free-public`, `cooldown_time` 120 and no budget. A key holding only that group reaches OpenRouter's free models and nothing paid. A future free-only route gets an access group of its own (`FREE_ACCESS_GROUPS` in `catalog/sync.py`), not a shared free group: LiteLLM grants a key a model name, not a deployment, so two routes in one group would hand each route's key the other's provider. `tests/config.test.mjs` fails on a free deployment outside its own group, and on any model name whose deployments carry two access groups.
- **OpenCode Go** lists extra fields: an item may carry `canonical_id` such as `zhipuai/glm-5.3`, which is stored as `metadata.canonical_id` and used for that route's canonical name (vendor segment dropped) before the alias table. Its base is `https://opencode.ai/inference/go/openai/v1`.
- **No rate limits.** No `rpm` or `tpm` is set on any deployment: a burst is served by the tier's weighted share, and a 429 cools the deployment down to the next tier.
- **The overview.** The same run publishes `README.md` beside `model-catalog.json` in the private `JorisJonkers-dev/litellm-model-catalog`: a summary per route (listed, usable, blocked, other), the canonical groups with their deployments in order, the usable models per route (free flag, context length, last probed, group) and the unavailable ones with the provider's reason, committed only when the content changes. The `local` route lists nothing while the GPU workstation is off or a game holds its GPU; the overview says so.

Published: ids, classification, reason text, free flag, context length, group and probe times; never a key, a prompt or a response body. The format of `model-catalog.json` follows the Hermes model-catalog schema that `JorisJonkers-dev/openrouter-model-catalog` generates (`version`, `providers.<route>.models[]` with `id`, `description: "free"` and `metadata`). `python3 catalog/sync.py` writes `model-catalog.json` and `model-overview.md` locally instead when `CATALOG_REPO` and `CATALOG_TOKEN` are not set.

Probe cost bound: each probe is about 8 input and at most 16 output tokens (doubled only for a provider that demands a higher minimum), once per model per day, at most 250 a run and 30 free OpenRouter models a run; free and local models cost nothing.

## Canonical models and routing

Agents ask for one canonical name per model (`mistral-large`, `devstral`, `gemini-2.5-pro`, `claude-sonnet-4-5`, `glm-5.3`); the `<route>/<id>` names keep resolving through the wildcards. The sync builds one LiteLLM model group per canonical name from `catalog/routing.json`: the deployments are the accounts whose probe says they serve the model (`usable`), ordered by which account serves it, not by model family.

| Order | Route | Weight | Used for |
|---|---|---|---|
| 1 | `local` | 1 | models the GPU host serves, when it is reachable |
| 2 | `google`, `anthropic` | 1 | Gemini (only in a `public/*` group); Claude (only here, never any other route or a free deployment) |
| 3 | `vibe` : `vibe-study` | 2 : 1 | every model the Vibe plan serves (Mistral, and GLM or DeepSeek when the probe shows the plan serves them) |
| 4 | `vibe-api` : `vibe-study-api` | 2 : 1 | the general Mistral API keys: fallback once the plan credit or quota is out, and the only home of API-only models (audio, OCR, embeddings) |
| 5 | `opencode-go` | 1 | only models none of the above serve (never a model Vibe, the general Mistral keys or Anthropic serve) |

The free `openrouter` route is not in these groups at all: it has its own `openrouter/<canonical>` groups (above). `local` is a normal tier. Orders are renumbered per group, so a model served only by OpenCode Go has order 1. `backup_only` keeps OpenCode Go out of a group any of the plan routes serves, and `exclusive` keeps `^claude` on `anthropic` alone.

**Budgets are a safety net, per deployment.** `catalog/routing.json` sets `max_budget` with `budget_duration: 30d` per route: `vibe` 255, `vibe-study` 127, `vibe-api` 25, `vibe-study-api` 12, `anthropic` 90, `opencode-go` 15, and none for `google`. LiteLLM caps a **deployment**, not an account, so a model served by two accounts of one plan gets the cap twice: the numbers are a runaway guard, not the plan limit. The plan limit is enforced by the provider, and exhaustion is handled by the cooldown above, not by the budget.

How LiteLLM v1.108 does it (read from its source): deployments sharing a `model_name` form a group; `order` picks the lowest level first and the router falls back to the next level when the level fails or is cooled down; a 402 or 429 cools a deployment down for its own `cooldown_time`; inside a level `weight` is a request-share pick (2:1 is a ratio of requests, not of spend); `max_budget` with `budget_duration` caps a deployment's spend and takes it out of the pick with `router_budget_limiting`. `router_settings` therefore holds only `optional_pre_call_checks: [router_budget_limiting]` and `num_retries: 2`: the cooldown and the allowed-fails policies are per deployment, in the generated file. Every request's serving deployment is in the spend log (`model_id`, `custom_llm_provider`, `api_base`) and on the request and error metrics (`model_id`, `api_provider`).

Canonical names come from one explicit mapping: normalisation (lower-case, vendor prefix, `:free`, `-latest`, a trailing version stamp are stripped) plus the `aliases` table in `routing.json`. Two ids that still differ are two models; nothing is merged by a fuzzy guess. The overview's `Possible duplicates (not merged)` section lists ids that look alike on different routes, so an alias can be added.

**Rollback.** Revert the generated-config pull request (or set `config/models.generated.yaml` back to `{"model_list": []}`) and redeploy the image: the wildcards then serve every `<route>/<id>` name as before, and canonical names stop resolving. Stopping the `litellm-catalog` Process stops new pull requests. Nothing has to be deleted from a database.

## Deployment

`deploy/litellm.project.yml` is the deploy-kit Project Intent: Project `litellm` in `litellm-system` at `litellm.jorisjonkers.dev`, with its environment in `deploy/env/litellm/base.env` and its secrets at `secret/data/platform/litellm` (`master_key`, `salt_key`, `vibe_api_key`, `vibe_study_api_key`, `vibe_api_general_key`, `vibe_study_api_general_key`, `opencode_go_api_key`, `openrouter_api_key`, `google_api_key`, `anthropic_api_key`, `catalog_publish_token`, `catalog_pr_token`). The catalogue Process gets the provider keys and both tokens; the proxy gets the provider keys only. CI validates both with the deploy-kit release `tools/deploy-kit` pins:

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

The image tests need Docker. They start the proxy read-only with only the project file's `writablePaths` writable, and point every upstream at a stub: they check wildcard routing and per-route credentials, the models list, the metrics and that no message text is logged. `npm run test:catalog` runs the catalogue sync's tests without Docker.

## Releases

release-please keeps a release PR from conventional commits. Merging it tags `vX.Y.Z`; the tag publishes a multi-arch image, reads it back by digest, runs the image tests against it, and publishes the Intent Fragment the estate composes. The LiteLLM version the image ships is recorded as the images lock's `version`.
