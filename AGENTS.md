# Agents

Guidance for any coding agent working in this repository.

- Estate conventions: https://github.com/JorisJonkers-dev/workspace/blob/main/CLAUDE.md
- Repository overview: [README.md](README.md)

The LiteLLM proxy image for the estate: route list in `config/config.yaml`, image in `Dockerfile`, deploy intent in `deploy/`, model catalogue sync in `catalog/`.

## Build and test

Scripts are defined in `package.json`; Node 24 or newer is required.

- All tests: npm test
- Catalogue tests only: npm run test:catalog
- Image test only: npm run test:image

## Free groups

A free model is served by several free routes, and a 429 on one should land on the next. `catalog/sync.py` therefore plans, next to each route's own `<route>/<canonical>` group, one shared `free/<canonical>` group holding every zero-cost copy of that model:

- **Which copies join.** The free-only routes that record a zero cost (not Inferbase, whose share is a dollar allowance) and the free ids of a paid route (OpenCode Go's `-free` ids). Google's free tier stays in `google-free/` and `public/`. `FREE_GROUP_ROUTES` in `sync.py` is the list.
- **How ids are matched across providers.** By `canonical_name()` and nothing else: the vendor prefix, a `:free` suffix, `-latest` and a date stamp are stripped, and case is folded, so `nvidia/nemotron-3-ultra-550b-a55b:free` on OpenRouter and `nvidia/nemotron-3-ultra-550b-a55b` on NVIDIA meet in `free/nemotron-3-ultra-550b-a55b`. An id the stripping does not unify is added to `aliases` in `catalog/routing.json`; the sync reports near duplicates and never merges them.
- **Order and cooldown.** `free_order` in `routing.json` gives the router `order` (the biggest allowance first: OpenRouter 1, Requesty 2, Kilo 3, NVIDIA and OVH 4; a route it does not name comes last), and each copy cools on its own `litellm_params.cooldown_time` from `cooldowns`. `allowed_fails` is unset, so a 429 cools the copy at once. Each copy has its own `model_info.id` (`free/<route>/<upstream id>`), so cooldown state is per copy. A route's own group and the shared group do not share cooldown state: both discover a rate limit separately.
- **Retries.** `router_settings.num_retries` stays above 0 on purpose: the retry is what lands on the next copy once the first is cooled.
- **The cost boundary.** `assert_free_groups()` runs at the end of the plan: every deployment in a `free/*` group must carry an explicit zero input and output cost, no budget, the access group `free` only and a free route, or the sync run fails. There is no `default_fallbacks` anywhere, and no fallbacks entry is keyed on a `free/*` group, so nothing reachable from one ends at a paid deployment.
- **Access.** A key holding the access group `free` reaches every `free/*` group, as it already reached the per-route ones.

`tests/test_free_groups.py` covers the plan and the assertion. Its Router tests (a 429 cools only that copy, the next serves) load the plan into LiteLLM's own Router with the upstream mocked, and run only where `litellm` is importable, so run them in the image: `docker run --rm -v "$PWD":/repo --entrypoint python <image> -W ignore -m unittest discover -s /repo/tests -p 'test_free*.py'`.

## Budgets

`accounts` in `catalog/routing.json` is the one source of spending caps: per paid account, the `routes` it bills, `max_budget` and `window` (`{"calendar": "month"}` is `1mo`, `{"days": N}` is `Nd`, an optional `anchor` is the declared cycle start). Owner caps (2026-11-18): OpenCode Go 40 per 30-day cycle (started 2026-11-04 10:41 UTC), Vibe 255, Vibe-study 127, `vibe-api` 25.5 and `vibe-study-api` 12.7 per calendar month (each its own counter; the `-api` windows assumed like their parents); `anthropic` 90 per 30 days and `inferbase` 0.5/day.

- **Mechanism.** `config/account_budget.py` (a `CustomLogger` in `config.yaml`'s `callbacks`, copied into the image) keeps one counter per account. `plan_groups()` writes `model_info.account` on every deployment of an account (`account_budgets()`), the tier copies inherit it, and `config/config.yaml`'s route wildcards carry it by hand. After each success the callback adds `response_cost` to the account; `async_filter_deployments` removes the deployments of a spent account from the pick and raises the router's budget error when none is left. Caps and windows come from `catalog/routing.json` (the image carries it). Windows are the declared ones: `window_start()` in `catalog/sync.py` gives the 1st of the month or the anchored cycle start, so nothing drifts to the first spend (only an unanchored `{"days": N}` window opens at its first spend). Proven in `tests/test_budgets.py`: a Router with the callback blocks the other models, the tier copies and the wildcard of a spent account and spares other accounts, and the real proxy (stub upstreams) did the same.
- **Why not LiteLLM's own counters.** `max_budget` counts per `model_info.id`. Giving every deployment of an account one id would share the counter, but the router keys cooldown and failover by id too: one 429 on one model cooled every model of the account, and a failover inside a group reached the same id twice (reproduced). So each deployment keeps its own id. Tag budgets (`tag_budget_config`) are Enterprise-only and the tag comes from the request. `provider_budget_config` is keyed by the LiteLLM provider, and the four Mistral accounts are all `mistral`.
- **Ids are not model names.** The router resolves a model name equal to a deployment's `model_info.id` to that one deployment and skips every pre-call filter (cooldown, LiteLLM budgets and this callback). A budgeted deployment's id is therefore `<route>:<upstream id>`, never `<route>/<upstream id>`, which a caller can send as a model name (reproduced: `vibe/<id>` walked past a spent cap). `tests/test_budgets.py` fails on a `/` in an account deployment's id.
- **Per-deployment backstop.** Each deployment keeps `max_budget` and `budget_duration` too: LiteLLM's own counter per deployment (its window opens at the first spend). Harmless: it can only stop a single model at the account cap.
- **Persistence.** The counters are in memory, and the first filtered request after a start seeds them from the proxy's spend log (`LiteLLM_SpendLogs`: spend summed per `model_id` since the earliest window start, mapped to accounts through the router's deployments, wildcards included). The query is bounded to 5 seconds; with no database or on any failure the callback logs it, serves, counts in memory and retries after 60 seconds. Spend not yet flushed to the log at restart is lost, and `tests/test_budgets.py` (`Seeding`) covers it with a fake prisma client; the SQL itself was not run against a real Postgres.
- **Sub-caps.** `vibe-api` and `vibe-study-api` are separate accounts. To merge one into another account, list its route there; the sub-cap is then gone.
- **Metrics.** The `model_id` label is `<route>:<upstream id>` for a paid deployment.

## Tier groups

`catalog/routing.json` is the single policy source for the tiers (`tier_groups`); agent-kit renders from it and holds no policy of its own. A tier is a callable model name with an ordered chain:

| Tier | Chain (cost class of each step) |
| --- | --- |
| `tier-free` | free only: Go free ids (`free/hy4-flash-free`, `free/longcat-2.5-free`, `free/beam-free`), then `free/glm-4.7-flash`, `free/qwen3.8-27b` |
| `tier-vibe` | Vibe Flash first (vibe), then Vibe Mistral Small, then `tier-go-cheap` (go) |
| `tier-go-cheap` | Go Flash models (go) |
| `tier-strong-open` | escalation only: `opencode-go-tier/qwen4-max` (go), then `tier-vibe`. **Owner decision pending:** Kimi K3.1 Pro and GLM-5.3, which the design put here, are opt-in, so this tier has one model of its own until the owner names others |
| `tier-review` | free models from different labs, then Vibe Flash (vibe), then Go Flash (go) |

The ladder is free, then Vibe, then Go. GLM-5.3, Kimi K3.1 Pro and GPT-6 Luna are opt-in only (`opt_in_only`): `validate_tiers()` refuses any chain that holds one, by canonical name, aliases included.

- **Declarative shape.** `tier_groups.<tier>` has `ceiling` (the dearest cost class the tier may hold), `key_models` and `steps`. A step is `{"model", "cost"}` or `{"tier"}`, which splices that tier's steps in place (first occurrence of a model wins, so the ladder cannot loop). The cost class must match the name's prefix (`free/` free, `vibe-tier/` vibe, `opencode-go-tier/` go; a route wildcard prefix is refused, see Budgets), and no step may exceed the ceiling. `tier-free` has ceiling `free`, so it cannot reach a paid deployment; `tier-review` reaches Vibe and Go only through steps that say so.
- **How it is rendered.** `config/config.yaml`'s `router_settings` holds a block between `# BEGIN tiers` and `# END tiers`: `model_group_alias` maps each tier to its first step, and `fallbacks` holds the rest in order. LiteLLM merges an included file's lists but replaces its mappings, so this block cannot live in `models.generated.yaml`. Regenerate it with `python3 catalog/sync.py --render-tiers`; a test fails if the file and `routing.json` disagree. The sync run also validates the tiers and names any free step whose `free/<canonical>` group the plan does not hold yet (the router skips a missing step and the next one serves).
- **Budgets (paid steps).** A paid step is never a route wildcard (the wildcards carry the account too, see Budgets above, but a tier step names a copy). A paid step is `vibe-tier/<upstream id>` or `opencode-go-tier/<upstream id>`, a copy (`tier_copies()`) of the route's own canonical deployment with the **same `model_info.id`** and `model_info.account`, budget and cooldown. A tier call and a direct call draw on one account counter (tested both ways). `assert_tier_copies()` fails the sync unless every copy shares an id with a deployment of its route and carries the route's budget from `routing.json`; a step the catalogue has no deployment for gets no copy, is unknown to the router and is skipped, never served by a wildcard. The cap is one counter per account (see Budgets above), shared with the route's other deployments. `tests/test_tiers.py` runs each budget scenario in its own process, because LiteLLM keeps budget state process-wide and only the first Router built enforces it.
- **No `default_fallbacks`,** and no fallbacks entry keyed on a `free/*` group.
- **Keys.** A key reaches a model name or access group it holds, and a tier name is neither (it is an alias), so a key lists the tier name beside what the chain needs. `enforce_fallback_model_access` is on in this LiteLLM, so a step the key does not hold is skipped: the key's list is what stops a chain, and it is the only thing that does. The lists, kept in `key_models` and tested against each chain:
  - the existing `free` key: `["free", "tier-free"]` (it already holds `free`; add `tier-free`);
  - `tier-vibe`: `["tier-vibe", "vibe-tier", "opencode-go-tier"]` (it ends in Go);
  - `tier-go-cheap`: `["tier-go-cheap", "opencode-go-tier"]`;
  - `tier-strong-open`: `["tier-strong-open", "vibe-tier", "opencode-go-tier"]` (an escalation key, not a workstation key);
  - `tier-review`: `["free", "tier-review", "vibe-tier", "opencode-go-tier"]`.
  `vibe-tier` and `opencode-go-tier` are access groups of the budgeted copies. No tier key holds `vibe/*` or `opencode-go/*`, so a tier key cannot reach the unbudgeted wildcards.

`tests/test_tiers.py` loads the rendered block into LiteLLM's Router (mocked upstream, run in the image), walks every chain with each step rate-limited in turn, and proves that a spent Go budget stops `tier-go-cheap`. `tests/image.test.mjs` boots the real image with this config and calls the tiers through the proxy.
