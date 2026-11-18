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
