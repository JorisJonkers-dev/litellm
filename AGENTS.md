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

## Tier groups

`catalog/routing.json` is the single policy source for the tiers (`tier_groups`); agent-kit renders from it and holds no policy of its own. A tier is a callable model name with an ordered chain:

| Tier | Chain (cost class of each step) |
| --- | --- |
| `tier-free` | free only: Go free ids (`free/hy4-flash-free`, `free/longcat-2.5-free`, `free/beam-free`), then `free/glm-4.7-flash`, `free/qwen3.8-27b` |
| `tier-vibe` | Vibe Flash first (vibe), then Vibe Mistral Small, then `tier-go-cheap` (go) |
| `tier-go-cheap` | Go Flash models (go) |
| `tier-strong-open` | escalation only: `opencode-go/qwen4-max` (go), then `tier-vibe` |
| `tier-review` | free models from different labs, then Vibe Flash (vibe), then Go Flash (go) |

The ladder is free, then Vibe, then Go. GLM-5.3, Kimi K3.1 Pro and GPT-6 Luna are opt-in only (`opt_in_only`): `validate_tiers()` refuses any chain that holds one, by canonical name, aliases included.

- **Declarative shape.** `tier_groups.<tier>` has `ceiling` (the dearest cost class the tier may hold), `key_models` and `steps`. A step is `{"model", "cost"}` or `{"tier"}`, which splices that tier's steps in place (first occurrence of a model wins, so the ladder cannot loop). The cost class must match the name's prefix (`free/` free, `vibe/` and `vibe-study/` vibe, `opencode-go/` go), and no step may exceed the ceiling. `tier-free` has ceiling `free`, so it cannot reach a paid deployment; `tier-review` reaches Vibe and Go only through steps that say so.
- **How it is rendered.** `config/config.yaml`'s `router_settings` holds a block between `# BEGIN tiers` and `# END tiers`: `model_group_alias` maps each tier to its first step, and `fallbacks` holds the rest in order. LiteLLM merges an included file's lists but replaces its mappings, so this block cannot live in `models.generated.yaml`. Regenerate it with `python3 catalog/sync.py --render-tiers`; a test fails if the file and `routing.json` disagree. The sync run also validates the tiers and names any free step whose `free/<canonical>` group the plan does not hold yet (the router skips a missing step and the next one serves).
- **No `default_fallbacks`,** and no fallbacks entry keyed on a `free/*` group.
- **Keys.** A key reaches a model name or access group it holds, and a tier name is neither (it is an alias), so a key lists the tier name beside what the chain needs. `enforce_fallback_model_access` is on in this LiteLLM, so a step the key does not hold is skipped: the key's list is what stops a chain, and it is the only thing that does. The lists, kept in `key_models` and tested against each chain:
  - the existing `free` key: `["free", "tier-free"]` (it already holds `free`; add `tier-free`);
  - `tier-vibe`: `["tier-vibe", "vibe/*", "opencode-go/*"]` (it ends in Go);
  - `tier-go-cheap`: `["tier-go-cheap", "opencode-go/*"]`;
  - `tier-strong-open`: `["tier-strong-open", "vibe/*", "opencode-go/*"]` (an escalation key, not a workstation key);
  - `tier-review`: `["free", "tier-review", "vibe/*", "opencode-go/*"]`.
  A key without `opencode-go/*` on `tier-vibe` stops at Vibe instead of falling to Go.

`tests/test_tiers.py` loads the rendered block into LiteLLM's Router (mocked upstream, run in the image) and walks every chain, rate-limiting each step in turn.
