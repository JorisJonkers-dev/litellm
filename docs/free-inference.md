# Free and cheap inference for agent-kit

Checked 2026-11-18. This page lists every free LLM source worth using: permanent or renewing free
tiers only. Trial credits, dead offers and unsuitable sources are left out, and are listed at the
end for reference. For each source it says where to make the account, what it gives, how it
plugs into this proxy, whether LiteLLM tracks it, whether it needs a harness of its own, and how
agent-kit reaches it.

**Verified** means seen on the provider's own page or public `/models` on 2026-11-18. **Partly**
means the page was seen but a figure came from elsewhere. Free tiers change weekly. The catalogue
sync probes every model before deploying it, and free-only routes deploy only ids marked free. A
wrong figure here therefore costs a failed probe, never a bill.

Every route here is for **public, open-source work only**: most free tiers may train on prompts.

## 1. How a source reaches agent-kit

**Shape A: an HTTP API through LiteLLM.** Most sources take this shape. Each provider becomes a
free-only route in this repository:

- its own access group, `<route>-free-public`, plus the shared `free` group;
- never in `canonical`, and never a fallback of a paid group;
- no budget, and a cooldown on 429.

The agent-kit `free` key already holds `free`, so every new route is reachable with no new key,
from every harness:

| Harness | How it reaches the free routes |
|---|---|
| Pi | `pi-free` (`litellm-free` provider in `~/.pi/agent/models.json`) |
| OpenCode | `opencode:litellm-free/<model>` |
| Hermes, Claude Code, Codex | `agent-kit-litellm run <harness> free --model <model>` |
| Model gateway | the `pi-free` client, and the `research` and `triage` profiles for inline public work |

LiteLLM records tokens, latency, status and a cost of 0 per request and per key. The Prometheus
metrics and the Grafana dashboard show them, and the catalogue overview shows what is usable.

**Shape B: only inside a vendor CLI.** The free quota cannot be called with an API key, or the
vendor forbids other clients. This needs a gateway client of its own, like `opencode-free`. Such a
client:

- has `data_scope: public`;
- checks the model is free before each run;
- counts usage from the CLI's output in the gateway ledger, which LiteLLM never sees;
- needs OS confinement before it is safe to run: agent-kit#903, and #904 for macOS, where
  `agent-launcher confine` cannot hold yet.

## 2. The list

**Columns:**

- **Shape:** A = through LiteLLM; B = its own CLI.
- **Tracked:** `yes` = LiteLLM per request; `ledger` = the gateway ledger only.
- **Status:** `have` = in place today; `add` = first wave; `later` = second wave.

| # | Source | Free allowance | Signup | LiteLLM | Shape | Tracked | Status | Verified |
|---|---|---|---|---|---|---|---|---|
| 1 | OpenRouter `:free` | 14 chat models; 20 RPM, 50 RPD per account, 1,000 RPD once $10 was ever bought | email | `openrouter/` | A | yes | have | yes |
| 2 | Google AI Studio | Gemini 4 Hydrogen, 3.8 and 3.6 Flash, 3.5 and 3.1 Flash-Lite, 3 Flash Preview, 2.5 Flash-Lite, Gemma 4; limits per model and project | Google account | `gemini/` | A | yes | have | yes |
| 3 | OpenCode Go `-free` ids | `beam-free`, `hy4-flash-free`, `longcat-2.5-free`, `iceman-contributor-free` | the Go subscription | `opencode_go/` | A | yes | have | yes |
| 4 | Local GPU (Ollama) | whatever the workstation serves | none | `openai/` | A | yes | have | yes |
| 5 | OpenCode Zen free models | 10 `-free` ids; LongCat 2.5 at 100 requests a day | OpenCode | none (client-gated) | B (`opencode-free`) | ledger | have | yes |
| 6 | Antigravity `agy` | Google AI account quota, not published, small | Google login | none (terms) | B (`agy`) | ledger | have | partly |
| 7 | Groq | gpt-oss-120b, gpt-oss-20b, qwen3.8-27b: 30 RPM, 1K RPD, 8K TPM, 200K TPD each | email or GitHub, no card | `groq/` | A | yes | add | yes |
| 8 | Cloudflare Workers AI | 10,000 Neurons a day over 60+ models; 300 RPM | email, no card | `cloudflare/` | A | yes | add | yes |
| 9 | NVIDIA build.nvidia.com | about 32 models; about 40 RPM | NVIDIA account | `nvidia_nim/` | A | yes | add | partly |
| 10 | Z.ai | GLM-4.7-Flash, GLM-4.5-Flash, GLM-4.6V-Flash | email | `zai/` | A | yes | add | yes |
| 11 | Kilo Gateway | 13 `isFree` ids; 200 requests/hour per IP | none (anonymous) | `openai/` | A | yes | add | yes |
| 12 | Requesty | 8 to 13 free models; 200 RPD, 20 RPM per org | email, no card | `openai/` | A | yes | add | yes |
| 13 | OVHcloud AI Endpoints | 7 zero-priced chat models; 2 RPM per IP per model | none (anonymous) | `ovhcloud/` | A | yes | add | yes |
| 14 | AxForge | 3M tokens every 30 days | email, no card | `openai/` | A | yes | add | partly |
| 15 | NagaAI | 20+ `:free` models; 10 RPM, 100 RPD | email | `openai/` | A | yes | add | yes |
| 16 | Routeway | 9 `:free` ids; 200 RPD, 1 concurrent | email | `openai/` | A | yes | add | yes |
| 17 | Inferbase | 5,000 requests a month; 100 managed requests and $0.50 usage a day | email, no card | `openai/` | A | yes | add | yes |
| 18 | GitHub Copilot Free | monthly AI-credit allowance, auto model only | GitHub account | `github_copilot/` (terms unread) | B (`copilot`) | ledger | add | partly |
| 19 | BazaarLink | 4 free models; 10 RPM, 60 weighted units a day | none or email | `openai/` | A | yes | later | yes |
| 20 | Electron Hub | about 24 `:free` ids | email | `openai/` | A | yes | later | partly |
| 21 | UnoRouter | about 170 `:free` ids, queue-throttled | none | `openai/` | A | yes | later | partly |
| 22 | Cohere | Command A family; 20 RPM, 1,000 calls a month | email | `cohere_chat/` | A | yes | later | yes |
| 23 | Api.Airforce | 1 RPM, 1,000 RPD; 4 of 26 free ids operational | Discord or email | `openai/` | A | yes | later | yes |
| 24 | Plugsky | own micro and lite models; 500 RPD, 250K TPD, 30 RPM | email, no card | `openai/` | A | yes | later | yes |
| 25 | Poe API | free-account daily points; 500 RPM | Poe account | `poe/` | A | yes | later | partly |
| 26 | Vercel AI Gateway | $5 every 30 days on free-tier models; 6 zero-priced ids | Vercel account, card | `vercel_ai_gateway/` | A | yes | later | partly |
| 27 | IBM watsonx.ai | 300K tokens a month, 20 CUH | IBM Cloud account | `watsonx/` | A | yes | later | yes |
| 28 | Novita | a few $0 ids | email | `novita/` | A | yes | later | partly |
| 29 | Together | `Prism-ML/Ternary-Bonsai-27B` free | account | `together_ai/` | A | yes | later | partly |
| 30 | Typhoon | free research API, typhoon-v2.5-30b-a3b | email | `openai/` | A | yes | later | partly |
| 31 | SEA-LION | free key, 10 RPM | email | `openai/` | A | yes | later | partly |
| 32 | Gonka Broker | about 1M tokens a month | real phone | `openai/` | A | yes | later | partly |
| 33 | Meituan LongCat API | 100K to 500K tokens a day (not confirmed) | phone | `openai/` | A | yes | later | no |
| 34 | Neosantara | `gemini-3.1-flash-lite` on a free account; other models need a deposit | email | `neosantara/` | A | yes | later | partly |
| 35 | Cline CLI | rotating free models, quota not stated | Cline account | none (not via its API) | B (`cline`) | ledger | later | partly |

## 3. The sources, one by one

### In place

**1. OpenRouter.**
- **What it gives.** 14 `:free` chat models today: Gemma 4 26B and 31B, Inkling 2 Small, Dots3-Note
  Preview (retiring 2026-12-31), Nemotron 3 Ultra, 3.5 Super VL and 3.5 Lightning, Nemotron 3 Nano
  Omni, Flint, North Mini Code, Ling 3.2 Flash, Particle-1, ERNIE 5.5 Flash Preview and LFM3 1.7B.
  All `:free` ids share one account-wide cap: 20 RPM and 50 RPD, or 1,000 RPD once $10 of credit
  has ever been bought.
- **Settings.** Deployed with `data_collection: allow`, and the account's "Free model training"
  setting is on.
- **Worth doing.** Buy the $10 once, for 1,000 RPD. It is never spent on free models.

**2. Google AI Studio.**
- **What it gives.** The free models are Gemini 4 Hydrogen, 3.8 Flash, 3.6 Flash, 3.5 Flash-Lite,
  3.1 Flash-Lite, 3 Flash Preview, 2.5 Flash-Lite and Gemma 4. Limits are per model and per
  project, shown only at https://aistudio.google.com/rate-limit. RPD resets at midnight Pacific.
- **Change on 2026-12-01.** `gemini-2.5-pro` re-routes to `gemini-3.8-flash` and `gemini-2.5-flash`
  to `gemini-3.5-flash-lite`, billed at those rates
  (https://ai.google.dev/gemini-api/docs/deprecations). Remove both from the `public/*` and
  `google-free/*` groups before then.
- **Data.** For a developer in the EEA, the paid data terms cover the free quota too: no training
  (https://ai.google.dev/gemini-api/terms).

**3. OpenCode Go `-free` ids.**
- **What it gives.** The free ids Go serves itself, through the `opencode-go` route, each alone in
  an `opencode-go-free/<canonical>` group.

**4. Local GPU.**
- **What it gives.** Ollama on the workstation, through the keyed gate (`local` route).

**5. OpenCode Zen.**
- **What it gives.** Free ids: `mimo-v2.6-flash-free`, `deepseek-v4.1-flash-free`, `longcat-2.5-free`,
  `iceman-free`, `inkling-2-small-free`, `beam-free`, `flint-40b-free`, `nemotron-3.5-super-vl-free`,
  `nemotron-3.5-lightning-free` and `hy4-flash-free`.
- **Limits of use.** OpenCode's docs say the free chat models "require OpenCode", and other clients
  get 403, so the proxy cannot serve them. The gateway runs them through `opencode-free`. Several
  of them train on prompts.
- **Unsafe until fixed.** That client is unconfined today: see #903.

**6. Antigravity `agy`.**
- **What it gives.** Google AI account quota through `agy -p --output-format json`. Launch it under a
  pty: without a TTY it loses stdout (antigravity-cli #76 and #318).
- **Terms.** Proxying its Google login is a terms violation (Gemini CLI FAQ), with suspensions
  reported, so it stays shape B.
- **Possible change.** `agy` in API-key mode (`"modelProvider": "gemini"`, `GEMINI_API_KEY`,
  `GOOGLE_GEMINI_BASE_URL` pointed at this proxy) would run it on the AI Studio quota, tracked by
  LiteLLM. This is untested.

### First wave (shape A)

**7. Groq.**
- **Account.** Sign up at https://console.groq.com, create a key at https://console.groq.com/keys,
  and check the limits at https://console.groq.com/settings/limits. Adding a card moves the
  organisation to pay-as-you-go, so do not add one.
- **Free plan.** From https://console.groq.com/docs/rate-limits: `openai/gpt-oss-120b`,
  `openai/gpt-oss-20b` and `qwen/qwen3.8-27b`, each at 30 RPM, 1K RPD, 8K TPM and 200K TPD; also
  `gpt-oss-safeguard-20b`, prompt guards, Whisper and Orpheus. Llama is no longer on the free plan.
- **Data.** Not retained by default; may be logged for up to 30 days for abuse review.
- **LiteLLM.** `groq/openai/gpt-oss-120b`, with `GROQ_API_KEY`. Tool calling works. Vault field
  `groq_api_key`.
- **Free signal.** The plan is free; `/models` needs a key and has no price. Keep the free ids as a
  static list in `routing.json`.
- **Catch.** 8K TPM: one long prompt can fail.

**8. Cloudflare Workers AI.**
- **Account.** Sign up at https://dash.cloudflare.com/sign-up (no card), then go to My Profile, API
  Tokens, and use the "Workers AI" template. The account id is in the sidebar.
- **Free tier.** 10,000 Neurons a day, reset at 00:00 UTC; past that a request fails and is never
  billed on Workers Free. Text generation is 300 RPM. `@cf/moonshotai/kimi-k2.6` and
  `@cf/zai-org/glm-5.2` need Workers Paid.
- **Data.** Not used for training.
- **LiteLLM.** `cloudflare/@cf/<org>/<model>`, with `CLOUDFLARE_API_KEY` and `CLOUDFLARE_ACCOUNT_ID`.
  The base is `https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1`. Vault field
  `cloudflare_api_key`; the account id goes in `base.env`.
- **Free signal.** None in the API. Keep a list of the free ids, without the Paid-only ones.
- **Catch.** Neuron cost differs per model.

**9. NVIDIA build.nvidia.com.**
- **Account.** Open any model at https://build.nvidia.com and click "Get API Key" (this joins the
  Developer Program).
- **Free tier.** The public `https://integrate.api.nvidia.com/v1/models` lists about 32 models,
  among them `nvidia/nemotron-3-ultra-550b-a55b`, `nvidia/nemotron-3.5-super-vl-120b-a12b`,
  `z-ai/glm-5.3-flash`, `moonshotai/kimi-k3.1-pro`, `mistralai/mistral-large-4-1t-a52b`,
  `google/gemma-4-31b-it`, `minimaxai/minimax-m3.1-flash`, `stepfun-ai/step-5-preview` and
  `poolside/laguna-xs-2.1`. About 40 RPM (forum figure). For prototyping only, and usage is logged
  to improve NVIDIA's products.
- **LiteLLM.** `nvidia_nim/<org>/<model>`, with `NVIDIA_NIM_API_KEY`. Vault field
  `nvidia_nim_api_key`.
- **Free signal.** Every listed chat model is free.
- **Catch.** Uneven availability; testers saw unusable days. Use it as a fallback.

**10. Z.ai.**
- **Account.** Register at https://z.ai/model-api and create a key at
  https://z.ai/manage-apikey/apikey-list.
- **Free models.** `glm-4.7-flash`, `glm-4.5-flash` and `glm-4.6v-flash` (vision), per
  https://docs.z.ai/guides/overview/pricing. About 1 concurrent request.
- **LiteLLM.** `zai/<id>` (LiteLLM 1.109 or later), with `ZAI_API_KEY`; base
  `https://api.z.ai/api/paas/v4/`. Set `input_cost_per_token: 0` and `output_cost_per_token: 0`:
  LiteLLM prices only `glm-4.5-flash` at 0. Vault field `zai_api_key`.
- **Free signal.** A static list of the three ids.
- **Catch.** GLM models reason by default, which uses more output tokens.

**11. Kilo Gateway.**
- **Account.** None: `https://ai-gateway.kilo.ai/api/v1/chat/completions` answered 200 with no
  `Authorization` header.
- **Free models.** The public `/models` marks 13 ids `isFree`: `kilo-auto/free`, `openrouter/free`,
  `reflection/beam-free`, and ten `:free` ids that mirror OpenRouter's. Each has a
  `mayTrainOnYourPrompts` flag. Its landing page showed only 3 at $0 at the same time, so probe
  each id. The limit is 200 requests an hour per IP.
- **LiteLLM.** `openai/<id>` with `api_base: https://ai-gateway.kilo.ai/api/v1`. LiteLLM's `openai/`
  wants a key, and whether Kilo accepts a dummy one is not tested.
- **Catch.** Per IP: the whole proxy shares one egress address, so it shares the 200 an hour.

**12. Requesty.**
- **Account.** Sign up at https://app.requesty.ai/sign-up (no card).
- **Free models.** https://docs.requesty.ai/features/free-models lists 13: the Nemotron 3 family,
  Laguna M.1 and XS.2, Gemma 4 31B, Leanstral 1.5, Muse Glimmer 30B, Ling 3.1 Flash and Ling 3.0
  Tiny. The limit is 200 a day and 20 a minute per organisation, over all free models together.
- **LiteLLM.** `openai/<id>` with `api_base: https://router.requesty.ai/v1` and `REQUESTY_API_KEY`.
  Vault field `requesty_api_key`.
- **Free signal.** In the public `/models`, `input_price == 0 and output_price == 0`; each model
  also has `data_used_for_training`.
- **Catch.** "Free for now."

**13. OVHcloud AI Endpoints.**
- **Account.** None for the anonymous tier.
- **Free models.** The public `/models` at `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` has
  `pricing`. Zero-priced chat models: `gemma-4-26b-a4b`, `nemotron3-nano-30b`, `laguna-xs-2.1`,
  `qwen3-vl-30b-a3b`, `DeepSeek-V4.1-Flash-1017`, `qwen3.6-35b-a3b` and `nemotron3-omni-30b`. The
  limit is 2 RPM per IP per model.
- **LiteLLM.** `ovhcloud/<id>`.
- **Free signal.** `pricing.prompt == 0 and pricing.completion == 0`.
- **Catch.** Per IP, and often 429. A last-resort copy of each model.

**14. AxForge.**
- **Account.** Sign up at https://console.axforge.ai (no card). The site says "3M free serverless
  tokens every 30 days". It is EU-hosted (Stockholm).
- **Free models.** `qwen3.8-27b`, `qwen3.8-flash-next`, `glm-5.3-flash` and `mimo-v2.6-flash`.
- **LiteLLM.** `openai/<id>` with `api_base: https://api.axforge.ai/v1` and `AXFORGE_API_KEY`.
  Vault field `axforge_api_key`.
- **Catch.** Check what happens at 3M before deploying. If it bills, keep it out.

**15. NagaAI.**
- **Account.** Sign up at https://naga.ac. The free plan, on its pricing page, is "20+ free models
  only" at 10 RPM and 100 RPD.
- **Free models.** The public `https://api.naga.ac/v1/models` has 22 `:free` ids (Ling 3.2 Flash,
  ERNIE 5.5 Flash, Inkling 2 Small, Particle-1, Flint, Gemma 4 and others).
- **LiteLLM.** `openai/<id>` with `api_base: https://api.naga.ac/v1`. Vault field `naga_api_key`.
- **Free signal.** The `:free` suffix.

**16. Routeway.**
- **Account.** Sign up at https://routeway.ai. Free plan: 200 requests a day, single concurrency, a
  low-priority queue.
- **Free models.** The public `https://api.routeway.ai/v1/models` has 9 `:free` ids (Muse Glimmer
  30B, Gemma 4 26B variants, DeepSeek V4.1 Flash, MiniMax M3.1 Flash).
- **LiteLLM.** `openai/<id>` with that base. Vault field `routeway_api_key`.
- **Free signal.** `:free`.

**17. Inferbase.**
- **Account.** Sign up at https://inferbase.ai (no card).
- **Free plan.** 5,000 requests a month, plus 100 managed requests and $0.50 of usage a day, on
  models priced up to $2 per 1M output tokens.
- **LiteLLM.** `openai/<id>` with `api_base: https://api.inferbase.ai/v1`. Vault field
  `inferbase_api_key`.
- **Catch.** The allowance is a dollar figure, so a model priced above $0 draws on it. The route
  needs a team `max_budget` matching $0.50 a day, so it can never bill past the free share.

### First wave (shape B)

**18. GitHub Copilot Free.**
- **What it gives.** A monthly AI-credit allowance (amount not published), auto model only.
- **Headless.** `copilot -p` with `COPILOT_GITHUB_TOKEN`, a fine-grained PAT with the "Copilot
  Requests" permission. Whether a Free account can use that PAT needs one manual check.
- **Tracked alternative.** LiteLLM's `github_copilot/` provider: an admin creates a credential, and
  the owner connects with a device code. Read GitHub's Copilot terms first.
- **Gateway.** A new `copilot` client, `data_scope: public`, sandboxed under #903 and #904.

### Second wave (shape A)

- **19. BazaarLink.** https://bazaarlink.ai/free. 4 free models (for example `qwen3.7-flash`,
  `deepseek-v4-flash:free`, `auto:free`): 10 RPM and 60 weighted units a day without credit, 20
  and 120 with credit, plus an IP guard. `openai/` with its base.
- **20. Electron Hub.** The public `https://api.electronhub.ai/v1/models` has about 24 `:free` ids
  (gpt-oss-120b, Gemma 4 31B, DeepSeek V4.1 Flash, GLM-5.x, Qwen3.8 27B, MiniMax M2.7). Its terms
  pages are JS-only. `openai/` with its base.
- **21. UnoRouter.** The public `https://api.unorouter.ai/v1/models` has about 170 `:free` ids,
  including Gemini 3.6 Flash, DeepSeek V4 Flash, GLM-5.3 Flash, Qwen3.8 27B, gpt-oss-120b and Kimi
  K3.1 Pro. No key is needed for free models, but they are queue-throttled (about 1 request a
  minute per model, reported). Per IP, like Kilo. `openai/` with its base.
- **22. Cohere.** Sign up at https://dashboard.cohere.com; a trial key is made at once. Chat models
  (Command A family, R+, R, R7B, North Mini Code) allow 20 RPM, and every trial key allows 1,000
  calls a month. Non-commercial. `cohere_chat/<model>` with `COHERE_API_KEY`. About one busy day of
  agent work a month.
- **23. Api.Airforce.** https://api.airforce. Free plan: 1 RPM and 1,000 RPD. In `/models`,
  `tier: "free"` marks 26 ids, 4 operational today (`gpt-oss-20b`, `llama-instant`,
  `kimi-k2.7-code`, `mistral-large-4`). `openai/` with `https://api.airforce/v1`.
- **24. Plugsky.** https://plugsky.com. Free tier: 500 RPD, 250K TPD, 30 RPM, 2 concurrent, no card.
  It covers its own micro and lite models only (30B-A3B, 262K context, tools). Good for background
  work. `openai/` with `https://api.plugsky.com/v1`.
- **25. Poe API.** https://creator.poe.com/docs/external-applications/openai-compatible-api. It runs
  on the account's points; a free account gets a small daily allowance (amount not known). 500 RPM.
  `poe/`. LiteLLM sees tokens, not points.
- **26. Vercel AI Gateway.** $5 of credit every 30 days on its "Free Tier" models, with lower limits.
  One user reports it did not renew. It needs a card on file (otherwise
  `403 customer_verification_required`). Six zero-priced ids in `/v1/models`
  (`nvidia/nemotron-3.5-lightning-free`, `reflection/beam-free`, `recursive/flint-v1.0`, and
  others). `vercel_ai_gateway/`. Buying credit ends the free monthly credit.
- **27. IBM watsonx.ai.** The trial plan gives 300K tokens a month and 20 CUH
  (https://www.ibm.com/products/watsonx-ai/pricing). `watsonx/`. Needs an IBM Cloud account.
- **28. Novita.** The public `/models` at `https://api.novita.ai/v3/openai` has a few
  `input_token_price_per_m == 0` ids (`bunny`, `nvrouter/auto`, `zai-org/glm-new`; "Apodex 1.1
  Mini"). `novita/`.
- **29. Together.** `Prism-ML/Ternary-Bonsai-27B` is listed free (262K context, vision, tools).
  Whether a key can call it without buying credit is not known: try it before adding.
  `together_ai/`.
- **30. Typhoon.** A free research API (`typhoon-v2.5-30b-a3b-instruct`, plus OCR), at
  `https://api.opentyphoon.ai/v1`. It collects usage data. Thai-focused. `openai/`.
- **31. SEA-LION.** AI Singapore's free key, 10 RPM, at `https://api.sea-lion.ai/v1`. `openai/`.
- **32. Gonka Broker.** About 1M tokens a month, reset monthly. It needs a real, non-VoIP phone, and
  one grant per number. The lineup rotates. `openai/` with `https://proxy.gonkabroker.com/v1`.
- **33. Meituan LongCat API.** Reported 100K to 500K free tokens a day, at
  `https://api.longcat.chat/openai/v1`. Not confirmed on a live page. LongCat 2.5 is already free
  through OpenCode Go and Zen.

- **34. Neosantara.** LiteLLM's page (https://docs.litellm.ai/docs/providers/neosantara) says a free-tier account can call `gemini-3.1-flash-lite`; other models answer 403 until a deposit. Docs: https://docs.neosantara.xyz. `neosantara/`.

### Second wave (shape B)

**35. Cline CLI.**
- **What it gives.** Rotating FREE-tagged models with an unstated quota. Its docs say they are not
  available through the Cline API.
- **Headless.** `cline "..." --json --auto-approve true`.
- **Gateway.** A `cline` client if the owner wants it. It is fragile, because the models and quota
  change without notice.

## 4. What this repository has to change, per route (shape A)

Groq is the example. The `openrouter` route is the pattern to follow.

1. **Vault.** The owner writes `groq_api_key` in `secret/data/platform/litellm` with estate's
   `./bootstrap/secrets.sh --only litellm`. Add the field to both `keys:` lists in
   `deploy/litellm.project.yml` (the proxy and the `litellm-catalog` Process). Anonymous routes
   (`kilo`, `ovh`, `unorouter`) have none.
2. **Environment.** Add `GROQ_API_KEY=${secret:...#groq_api_key}` and
   `GROQ_API_BASE=https://api.groq.com/openai/v1` to `deploy/env/litellm/base.env` and the
   catalogue's env file.
3. **Credential.** Add an entry to `credential_list` in `config/config.yaml`:
   `{ credential_name: groq, credential_values: { api_key: os.environ/GROQ_API_KEY }, credential_info: {} }`.
   Add no wildcard: only generated free deployments reach a free route.
4. **Sync** (`catalog/sync.py`):
   - Add a `ROUTES` entry with a `kind`.
   - Add the prefix to `PREFIXES`.
   - Add the kind to `FREE_KINDS`, and `<route>-free-public` to `FREE_ACCESS_GROUPS`.
   - In `list_models`, add the free signal: `isFree` (Kilo); a zero price (Requesty, OVH, Novita,
     Vercel); a `:free` suffix (Naga, Routeway, Electron Hub, UnoRouter); `tier == "free"`
     (Airforce); or everything listed (NVIDIA).
   - For the plan-wide free tiers (Groq, Cloudflare, Z.ai, Cohere, AxForge, Plugsky, Inferbase),
     use a static `free_ids` list in `routing.json`. Then a paid id can never be deployed on a
     route that must not bill.
   - Add `"anonymous": true` for `kilo`, `ovh` and `unorouter`, so a route without a key is listed
     and probed instead of skipped. The probe already omits `Authorization` when the key is empty.
   - Add a limit per route in `DEFAULT_ROUTE_LIMITS`, below the provider's daily cap: Requesty,
     Naga and Routeway 100 to 200 a day, Cohere 1,000 a month, OVH and Airforce a handful.
5. **Routing rules** (`catalog/routing.json`). Add a cooldown per route: 120 s, or 3600 s for daily
   caps (`requesty`, `naga`, `routeway`, `cohere`, `airforce`). Set no budget, except Inferbase
   (above). Keep free routes out of `tiers`.
6. **Zero prices.** Every free deployment the price map does not know gets
   `input_cost_per_token: 0` and `output_cost_per_token: 0`, so it records a real 0, not an unknown.
7. **Tests.** Cover a dangling credential and a free deployment outside its group (both already
   checked), each kind's free signal, a paid id on a free route never being deployed, and an
   anonymous route being probed without a key.
8. **One group per model, across free providers (new).** The same model is often free in several
   places: Nemotron 3 Ultra on OpenRouter, Kilo, Requesty, NVIDIA and Routeway. Today each free
   route has its own `<route>/<canonical>` group. A shared `free/<canonical>` group, ordered by
   limit, would let the router fall through on 429 to the next free copy before failing. It must
   hold free deployments only. This is the change that turns many small allowances into one large
   one.

Then, in agent-kit:

- Add each `<route>-free-public` to `LITELLM_PUBLIC_GROUPS` in `scripts/render_registry.py`. No new
  key is needed: the `free` route holds `free`.
- Refresh `registry/litellm-catalogue.json` and render, so `pi-free` lists the new groups.
- Give each useful free model a `tier` and notes in `defaults.json` (`"client": "pi"`). Without
  them the delegate skill never picks a model, because its tier is null. ClawLabs' daily
  `data/models.json`
  (https://raw.githubusercontent.com/ClawLabsAI/free-ai-models/main/data/models.json) has a quality
  score per model to base the tier on.
- For shape B (`copilot`, `cline`): add a client in `defaults.json` with `data_scope: public`, a
  launcher in `scripts/crates/model-gateway/src/launchers.rs`, an output parser for the ledger, and
  a row in the harness matrix. Do this only once #903 and #904 confine the launch.

## 5. What it adds up to

With the first wave, roughly:

- **Requests a day:** about 3,000 to 5,000, after per-model and per-IP limits. Groq alone gives
  3 × 1K RPD; OpenRouter gives 1K after the $10; Naga, Routeway and Requesty give 100 to 200 each.
- **Tokens:** 200K a day per Groq model, 10K Neurons a day on Cloudflare, 3M a month on AxForge.
- **Models:** about 60 distinct free chat models.

The second wave adds UnoRouter's 170 queue-throttled ids and some extra headroom.

**Caveats:**

- Per-IP limits (Kilo, OVH, UnoRouter) are shared by every agent, because the cluster has one
  egress IP.
- Several free models train on prompts. The routes are public-work only, and the render and the
  gateway enforce it.
- Free models are weaker than paid ones and go down more often. Keep paid routes for work that
  needs them.

## 6. Paid plans through the proxy (not free, for reference)

For non-`nedap` work only:

- **Claude Max.** Not routed through the proxy (owner decision). Claude Code talks to Anthropic directly, and usage is tracked in the gateway ledger only.
- **Codex / ChatGPT.** Left out of personal work (owner decision). It stays nedap-only.
- **Antigravity.** Its Google login cannot go through the proxy (Google's terms). See source 6 above for
  API-key mode.
- **Copilot.** See source 18 above.

## 7. Left out on purpose

| Left out | Why |
|---|---|
| Trial credits (Inception 100M until its API closes 2026-12-28, Cerebras $5, Scaleway 1M, Alibaba Model Studio 90 days, Tencent, Volcengine, Baidu, AI21, Upstage, Sarvam, DeepSeek grant, Anthropic $5, Xiaomi $2, GMI, Together $5, Azure $200, Novita referral) | one-time; they run out |
| OpenAI data-sharing tokens | needs a paid org, not confirmed |
| ModelScope, bigmodel.cn, SiliconFlow, iFlytek Spark | Chinese real-name verification; `zai` serves the same GLM models |
| AI Horde, Aion Labs, Pollinations | too small for agents (2 to 8K context, 20K tokens a day), roleplay-only, or non-commercial |
| Resellers handing out "free" frontier models (xKiro, Septor, NaraRouter, DreamPrompting, BlazeAPI and the rest of the freellmapi catalogue) | not verified, and "free Claude-class" access points at resold or abused keys |
| Kiro, Cursor, Grok Build, Windsurf, Trae, Zed, Warp, Amp, Factory, Jules | no free headless quota, or terms that bar wrapping, or not a delegate shape |
| Dead | GitHub Models (2026-07-30), Mistral free API (Free mode "doesn't include API access"), Hugging Face free credit, Qwen OAuth (2026-04-15), iFlow (2026-04-17), Gemini CLI free tier (moved to `agy`), Amazon Q (closed), Ollama Cloud, LLM7, Chutes, Nebius, Featherless, DeepInfra, Parasail, Inference.net, Lambda, Hyperbolic, Venice, Moonshot, MiniMax, StepFun, Reka, xAI, Writer, Fireworks, Jina, Puter, Martian, Kluster, Targon, Zuki, AnyAPI, B.AI, Empero |

## 8. Orchestration: getting the most from every subscription

## Decisions (owner, 2026-11-18)

These were settled in a grilling session, and they override anything below that disagrees.

**Architecture and hosting**

- **Architecture.** Claude Code is the interactive hub. Gold Band runs the interactive workflows (its workflows stay files in agent-kit, and it picks models by tier). Hermes is hosted in k3s as a deploy-kit Project, in a new repository `JorisJonkers-dev/hermes`, and runs the loops and cron jobs. LiteLLM is the only API gateway, and the model-gateway is the only CLI layer.
- **Hermes.** Upgrade from v0.19.0 to v0.23.2 and pin it. It holds LiteLLM keys only, never a subscription login. Its pod is its sandbox: no host mounts, a throwaway clone, non-root, a read-only root filesystem, and egress limited by NetworkPolicy to LiteLLM and GitHub.
- **Renovate repair loop.** Runs on Hermes, on public JorisJonkers-dev repositories only. It pushes fixes to the Renovate branch and merges patch and minor updates only once the required checks pass. A major update gets a comment for the owner. Models go free → Vibe Flash → Go Flash; when Flash cannot fix a PR, the loop comments and stops. It never escalates to Claude. It uses a fine-grained PAT.

**Subscriptions**

- **Claude.** Claude subscription traffic never enters LiteLLM, so the `claude-max/*` route is dropped. The personal Max x5 plan and the work Claude Team plan may both run parallel `claude-code` workers. They are tracked in the gateway ledger only.
- **Codex.** Left out of personal work entirely: nedap only, under the existing grant rule.

**Tiers and models**

- **Ladder.** free → Vibe → Go → strong-open → claude-code. Vibe comes before Go, because Vibe is subsidised and Go draws on a shared allowance.
- **OpenCode Go.** Hard cap: 60 EUR per 30 days on the key, Extra Usage off, no top-ups. The gateway drops a tier at 80% of the 5-hour and weekly windows.
- **Reviews and red-teaming.** Free models first, then Vibe Flash, then Go Flash. GLM-5.3, Kimi K3.1 Pro and GPT-6 Luna only when the owner asks.
- **Policy source.** `catalog/routing.json` in this repository holds the tiers and fallbacks. agent-kit only renders them.

**Free providers and the network**

- **Second IP.** Not used. One egress IP, so the dual-IP design below is dropped.
- **Accounts.** The owner creates the first-wave accounts (Groq, Cloudflare, NVIDIA, Z.ai, Requesty, AxForge, NagaAI, Routeway, Inferbase). Kilo and OVH need none. The OpenRouter $10 is already bought.
- **Neosantara** (`neosantara/`, a free account can call `gemini-3.1-flash-lite`) joins the second wave. A sweep of all 142 LiteLLM provider pages on 2026-11-18 found no other new permanent free tier. Nscale and CometAPI give sign-up credit only.

**Order of work**

- **Sandbox first.** agent-kit#903 and #904 land before any new laptop CLI client (`copilot`, `cline`). `copilot` follows the sandbox, after a manual check that a Free-plan PAT works.
- **First workflow to port.** Cross-lab spec critique (`review-spec`).


This section answers the owner's integration brief of 2026-11-18. That brief asked for an options analysis, a tier table, tickets, open questions and unverified facts. Free and cheap models did the research and the merge: four Vibe delegates and one DeepSeek V4.1 Flash synthesis, $0.27 in all. Their inputs were the agent-kit tree, the Hermes issues, releases and docs, LiteLLM's routing docs, and OpenCode Go's live model list.

Corrections applied after the owner's review:

- Reviews and red-teaming use free models and Flash models first. GPT-6 Luna, GLM-5.3 and Kimi K3.1 Pro are opt-in only, because they cost too much for routine passes.
- The Vibe tier starts on Flash.
- Hermes **v0.19.0** is installed. The v0.23.2 the text cites is the latest release.

Re-checked facts (GitHub, 2026-11-18):

- **Hermes PR #32401** (generic ACP client transport) is open.
- **#5257** is open, labelled `needs-decision`.
- **#36057** is open.
- **#49332** is closed as a duplicate of #17685: the per-call model override is ignored.

### Options analysis

Scores 1–5, 5 = best (for criterion 6, 5 = least churn).

| Option | 1 Quota | 2 Quality | 3 Kit fit | 4 Config-as-code | 5 Observability | 6 Churn | Total |
|---|---|---|---|---|---|---|---|
| A Claude-centric (Claude Code coordinates; PAL clink/acpx to CLIs) | 2 | 4 | 3 | 3 | 2 | 3 | 17 |
| B Hermes-centric (cheap brain coordinates; CLIs as workers) | 4 | 2 | 2 | 4 | 3 | 1 | 16 |
| C Hybrid (Claude Code hub + Hermes background, one CLI layer, one API gateway) | 5 | 4 | 4 | 4 | 4 | 3 | 24 |
| D ACP-client-centric (Gold Band) | 3 | 3 | 2 | 3 | 2 | 2 | 15 |
| E Self-hosted always-on hub (Hermes daemon + cron beside LiteLLM) | 5 | 3 | 3 | 4 | 4 | 2 | 21 |

Reasoning per option: **A** puts the most expensive, most rate-limited subscription in the coordination seat and depends on `acpx`/`claude-agent-acp`, none of which are installed. **B** absorbs quota best but makes a churning, silently-substituting agent the brain and duplicates the gateway. **C** keeps Claude Code interactive, spends free/Go/Vibe on everything else, and reuses the existing gateway and critique pipeline. **D** is a pinned AGPL desktop app whose workflows ADR-0014 deliberately keeps outside the gateway. **E** is C plus overnight scheduling, but adds server-side CLI logins and daemon ops.

Spread: quota and quality trade off cleanly — every option that spends Claude on coordination loses criterion 1, every option that hands coordination to a cheap model loses criterion 2. Churn is where B and E collapse: Hermes v0.23.2 alone rolled up 1,989 PRs since v0.23.1, and the generic ACP client (#32401, #5257, #68222) is still unmerged.

**Challenging the hybrid.** The kit already contains most of what C needs, so C is not a new architecture — it is a wiring job:

- **One CLI layer = the Rust model-gateway** (`scripts/crates/model-gateway/`). It already spans `--harness claude|codex|agy|vibe|hermes|opencode|opencode-free` plus Pi routes (`pi-vibe`, `pi-opencode-go`, `pi-free`, `pi-openrouter`, `pi-public`), and already has grants (ADR-0008), `usage`, ladder escalation, refusals and `docs/model-gateway/harness-matrix.json`. ADR-0007 already names it the single delegation gateway. Do not add a second transport: ACP is uninstalled and upstream-unmerged, PAL clink is not in the dump, and Gold Band is a workflow surface, not a transport.
- **One API gateway = LiteLLM**, already carrying virtual keys (`vibe` €255/30d, `vibe-study` €127.5, `opencode-go` €60, `public` €5, `free`, `local`).
- **Hermes adds value only as a scheduler and cheap fan-out brain** — batch `delegate_task` (10 concurrent default), cron one-shots, `auxiliary.*` repointing, and per-result `route` as a serving-model ledger. It adds no value as a CLI transport: `delegate_task` has no `model` parameter by design, Claude Code has no ACP mode, and logging Claude into Hermes bills extra-usage credits while the Max allowance sits untouched.
- **Gold Band stays where it is** (ADR-0014, outside the gateway) as the interactive workflow surface; the second instance is only worth wiring if it is genuinely config-as-code via `scripts/converge.py gold-band` + `keep.yaml`.

**Recommendation: C, with E's scheduling adopted as one bounded piece.** Where each piece runs:

| Piece | Host | Why |
|---|---|---|
| Claude Code CLI (2× Max x5), interactive hub, `/implement`, `/grill-me` | laptop | subscription traffic must stay in the official CLI; interactive latency |
| model-gateway (`run`/batch, `--tier`) | laptop | already there; needs the repo and the CLI logins |
| Gold Band desktop + personal instance | laptop | Tauri desktop app |
| LiteLLM + Redis + Postgres spend logs + Prometheus | k3s | always-on, already at `litellm.jorisjonkers.dev` |
| `egress-proxy` (IP-B) | home server or k3s node pinned `egress: ip-b` | second public IP |
| Hermes daemon + cron (research, red-team, overnight batch) | home server | always-on, no interactive login, no repo write access |
| Pi routes | home server / k3s, **not** macOS | #904 blocks Pi on a Mac |

### Tier table

Two planes: LiteLLM owns tiers 0–4; the model-gateway owns `tier-claude-code` and the mapping. Claude subscription traffic never enters LiteLLM.

| Tier | LiteLLM model name | Concrete model / route | Ordered fallbacks | Parallelism cap |
|---|---|---|---|---|
| `tier-free` | `tier-free` (routing group) | `free/hy4-flash-free`, `free/longcat-2.5-free`, `free/beam-free` (Go free, no allowance draw) | `free/glm-4.7-flash` → `free/qwen3.8-27b` → `local/*` | 4/deployment, 6 harness |
| `tier-go-cheap` | `tier-go-cheap` | `opencode-go/deepseek-v4.1-flash` ($100 pool, full tool-choice, 1M ctx) | `opencode-go/glm-5.3-flash` → `opencode-go/mimo-v2.6-flash` → `tier-vibe` | 2 |
| `tier-vibe` | `tier-vibe` | `vibe/deepseek-v4.1-flash` (subsidised) | `vibe/zai-glm-5-3-flash` → `vibe/mistral-small-latest` → `tier-go-cheap`; `vibe/mistral-large-4-0-3` only as an explicit escalation | 2 |
| `tier-strong-open` | `tier-strong-open` | `opencode-go/kimi-k3.1-pro` ($15 pool — escalation only) | `opencode-go/qwen4-max` → `opencode-go/glm-5.3` → `tier-vibe` | 3 |
| `tier-review` | `tier-review` | free models from different labs first (`free/nemotron-3-ultra`, `free/gemma-4-31b`, `free/glm-4.7-flash`, `free/gpt-oss-120b`) | `opencode-go/qwen3.8-flash` → `opencode-go/minimax-m3.1-flash` → `opencode-go/mimo-v2.6-flash`; `opencode-go/gpt-6-luna` only when a review needs it | 3 |
| `tier-claude-code` | *not in LiteLLM* | Claude Code CLI, Max x5 | none — never falls back | 2 (one per Max plan) |

Notes that change behaviour: `glm-5.3`'s 500 pool is promotional through **2026-11-22** (four days out) — do not size the coordinator budget on it. `deepseek-v4.1-flash` carries a 2× multiplier Mon–Fri 01:00–04:00 and 06:00–10:00 UTC, so schedule Hermes batch outside those windows. `deepseek-v4.1-flash` needs Global region and its ZDR agreement expires 2026-11-30. `glm-5.3`/`glm-5.3-flash` accept tool `choice: ["auto"]` only. Mistral Large 4 is absent from Go entirely, so Vibe is the only zero-Go-cost coordinator fallback. The brief's ladder puts Go above Vibe; Vibe is subsidised, so within `tier-vibe` prefer Vibe deployments and treat Go as the fallback (flagged as an open question).

**Where the config lives as code.** `catalog/routing.json` in the litellm repo is the single source of truth for tiers, fallbacks, per-route rpm/rpd/cooldown/egress. `catalog/sync.py` emits `config/config.yaml` and a `tiers` block into agent-kit's `registry/litellm-catalogue.json` — that file is a rendered artifact and must hold no policy. In agent-kit, `registry/delegation-profiles.yaml` gains one profile per tier (`tier-free`, `tier-go-cheap`, `tier-vibe`, `tier-strong-open`, `tier-review`), rendered by `scripts/render_registry.py` into `skills/workstation/model-gateway/defaults.json`; `render_registry.py --check` stays the CI gate.

### Free-model fallbacks (the two-IP part is dropped: one egress IP)

**Group invariant.** One group per canonical model, `free/<canonical>`, containing only zero-cost deployments; `paid/<canonical>` for everything else. A key is granted a model *name*, not a deployment, so the group boundary is the only real cost boundary. Assert it in `catalog/sync.py` (`input_cost_per_token == 0 and output_cost_per_token == 0` for every `free/*` deployment) and test every chain reachable from `free/*` and `tier-free`.

**Ordering.** `order` by allowance — OpenRouter 1, Requesty 2, Kilo 3, NVIDIA/OVH 4 — with `weight` inside a tier, `enable_weighted_failover: true` under `routing_strategy: simple-shuffle`, `num_retries: 0` (a 429 will not succeed on retry), and per-deployment `model_info.cooldown_time` (3600s for daily caps, 60s for per-minute) with `allowed_fails` **unset** so a 429 cools immediately. Give every deployment an explicit `model_info.id` so cooldown state cannot collide. Never set `default_fallbacks` — one stray entry silently routes every free group to a paid tier. Keep `enforce_fallback_model_access: true`.

**Access groups.** Every free deployment carries `model_info.access_groups: ["free", "<route>-free-public"]`, so a key created with `"models": ["free", "tier-free"]` picks up new routes without re-issue. Routing groups are their own model name, so the `free` key needs both.

**Two IPs.** The only documented lever is per-deployment `api_base` pointing at a small reverse proxy on host B: `scripts/crates/egress-proxy/` (axum, `/<route>/v1/*` → upstream, strips the prefix, forwards `Authorization` verbatim), deployed with `nodeSelector: {egress: ip-b}`. Use the `openai/` prefix with an explicit `api_base` for every IP-B copy, even for providers with a native prefix. `HTTPS_PROXY` is process-wide and a per-deployment proxy key is undocumented — do not rely on either.

**ToS caveat.** Two IPs buy isolation and concurrency, not quota. Halve `rpm` per copy (Kilo `rpm: 1.5` on each of two copies, not `rpm: 3` on both) so the aggregate stays at the per-IP budget; put interactive traffic on IP-A and batch on IP-B. Doubling a per-IP limit is evasion, and per-account limits (OpenRouter 20 RPM, Requesty 200 RPD) are unaffected by IPs entirely. This is a reading of the terms, not legal advice — **UNVERIFIED**.

### Using Hermes

**Role.** Background and scheduled fan-out only: overnight research, red-team passes, cross-lab critique batches, triage. Never the CLI transport, never the interactive hub, never a Claude login.

**Config** (`~/.hermes/config.yaml`, secrets in `~/.hermes/.env`): one named provider per LiteLLM virtual key (`litellm-free`, `litellm-go`, `litellm-vibe`, `litellm-strong`, `litellm-local`), each `base_url: ${LITELLM_BASE_URL}` + `key_env`. `model.default` on the Vibe route; `delegation.model`/`delegation.provider` pinned to `litellm-free`; `delegation.fallback_providers: ["litellm-google", "litellm-local"]` with no paid route in the chain; `delegation.max_concurrent_children: 10`, `max_iterations: 60`, `child_timeout_seconds: 1800`. Repoint `auxiliary.compression`, `auxiliary.titles`, `auxiliary.vision` off the main model — by default they bill at the main model's rate.

**Plugin.** `~/.hermes/plugins/delegation-litellm-tiers/` implementing `DelegationProvider` with a `tier` enum (`free`, `go-cheap`, `vibe`, `strong-open`, `review`) mapped to LiteLLM groups, `fallback_providers: []` on every paid tier, and `DelegationRouteError` for unknown tiers. Core enforces the enum before `route_task` runs, and every non-default-backend result carries `route` (actual provider/model/effort) plus `fallback_activated` — that is the serving-model ledger. `route_task` runs pre-spawn, so it cannot refuse a completed child; the enum guard, the fallback pins and a post-hoc audit cron are the defence.

**Cron.** `hermes --yolo chat -Q -q "<task>" -m <cheap model> --provider litellm-free -t file,web`, scheduled from the top level (subagents cannot create cronjobs). `--yolo` plus a deliberately narrow toolset is required because one-shot runs have no approval channel.

**What not to do.** Do not log Claude into Hermes — Anthropic OAuth there consumes extra-usage credits and leaves the Max allowance untouched. Do not expect a per-task `model` parameter on `delegate_task`; it was removed deliberately. Do not build on the ACP client path until #68222 merges and its `mcpServers: []`, repr-command and dropped-`error.data` defects clear. Do not run 0.19.0 — upgrade to 0.23.2 first (`hermes fallback check`, durable subagents, idle-task APIs). Do not give it repo write access or a working directory it can escape (#903).

### Adapting the skills

| Skill / workflow | Change |
|---|---|
| `delegate` | Add a tier-selection step before difficulty rating; read `opencode.allowance` and LiteLLM `/spend` first; emit a routing record per task. Keep "one batch, verify". |
| `model-gateway` skill + `defaults.json` | Add a `tiers` block and a `--tier` flag; profiles stay the narrowing mechanism (a request may narrow, never widen). |
| `critique`, `cross-critic` profile | Free models first, then Go Flash models (`qwen3.8-flash`, `minimax-m3.1-flash`, `mimo-v2.6-flash`); `gpt-6-luna` as an opt-in; raise `distinct_families` to 3; keep `exclude_writer_family: true`. No GLM-5.3 or Kimi K3.1 Pro by default: too expensive. |
| `review-spec`, `review-tickets`, `review-pr`, `review-architecture` | Route to `tier-review` instead of `cross-critic`; keep the ledger and `critique-stats` routing suggestions. |
| `research` | Keep the Gold Band switch; add a Hermes cron path for overnight runs. |
| `council/` | Contents unverified — leave untouched until confirmed. |
| Pocock pipeline | `/grill-me` and `/grill-with-docs` stay interactive Claude Code. `/to-spec` and `/to-tickets` stay as Gold Band workflows (`to-spec.json`, `to-tickets.json`). `/implement` stays Claude Code. `/tdd` routes to `tier-go-cheap`; `/code-review` routes to `tier-review`. |
| Gold Band | Keep as the interactive workflow surface, outside the gateway (ADR-0014). Second instance only for cross-lab review, and only if `converge.py gold-band` + `keep.yaml` keep it config-as-code. |

### Tickets

1. **Free-group invariant in the catalogue** — litellm. *Why:* the cost boundary is a group boundary. *Change:* `catalog/routing.json` + `catalog/sync.py` emitting `free/<canonical>` groups; build-time assertion that every `free/*` deployment is zero-cost. *AC:* the catalogue tests fail if a paid deployment lands in a `free/*` group. *Test:* unit test over a fixture with one paid copy injected. *Deps:* none.
2. **Free deployments with order/weight/cooldown** — litellm. *Why:* 429 handling is the whole free-tier story. *Change:* four Go free models + OpenRouter/Requesty/Kilo/NVIDIA copies with `order`, `weight`, `model_info.id`, `cooldown_time`, `allowed_fails` unset. *AC:* a simulated 429 cools only that deployment. *Test:* router fixture asserting fall-through order. *Deps:* 1.
3. **Tier routing groups and fallback chain** — litellm. *Why:* tiers must be callable names. *Change:* `tier-free`…`tier-review` as `routing_groups`; `fallbacks` chain; no `default_fallbacks`. *AC:* `tier-free` never resolves to a paid deployment. *Test:* config-load test asserting no `default_fallbacks` key and a terminal `tier-strong-open`. *Deps:* 2.
4. **Per-tier virtual keys** — litellm. *Why:* cap Go before the provider does. *Change:* keys with `models: ["free","tier-free"]` etc., `max_budget`/`budget_duration` sized to the Go allowance, `metadata.disable_fallbacks` on the Claude-adjacent key. *AC:* `tier-go-cheap` 429s at the cap and the chain falls to `tier-vibe`. *Test:* integration test against a stub upstream. *Deps:* 3.
5. **(dropped: the second IP is not used)**
6. **Tier logger, metrics and paid-leak alert** — litellm. *Why:* observability is criterion 5. *Change:* `tier_logger.py` callback emitting `litellm_tier_requests_total{tier_requested,tier_served,deployment,route,egress,status}`, `litellm_fallback_total`, plus the `LiteLLMPaidLeak` Prometheus rule. *AC:* a forced fallback produces `tier_requested != tier_served` in metrics. *Test:* Prometheus rule unit test with a synthetic series. *Deps:* 3.
7. **Tier profiles in the registry** — agent-kit. *Why:* the gateway needs the tier names. *Change:* five profiles in `registry/delegation-profiles.yaml`, rendered to `defaults.json`. *AC:* `render_registry.py --check` passes; each profile lists only its tier's models. *Test:* `tests/` assertion that no profile mixes tiers. *Deps:* 3.
8. **`model-gateway run --tier`** — agent-kit. *Why:* one flag instead of per-model selection. *Change:* map `--tier` to the LiteLLM group; unknown tier is an error, never a silent default. *AC:* `--tier tier-free` sends `model: tier-free`; `--tier nope` exits non-zero. *Test:* fixture `run-tier` alongside `run-profile`. *Deps:* 7.
9. **Routing ledger record per run** — agent-kit. *Why:* the done-criterion log. *Change:* extend the `usage` surface to record tier, model, harness, cost, fallback per task. *AC:* one JSON line per delegate run with all five fields. *Test:* fixture asserting the record shape. *Deps:* 8.
10. **Ingest `x-litellm-model-id` and `x-litellm-attempted-fallbacks`** — agent-kit. *Why:* requested-vs-served for shape-B clients LiteLLM never sees. *Change:* read both headers in the gateway and write them into the ledger. *AC:* a forced fallback shows `attempted_fallbacks: 1`. *Test:* stub server returning the headers. *Deps:* 9.
11. **Quota precheck and fallback-before-limit** — agent-kit. *Why:* quota cliffs. *Change:* accumulate `opencode.allowance` normalised by each model's `go.monthly_usage.go`, compare against 20%/50%/100%, read LiteLLM `/spend`, drop a tier before the hard limit. *AC:* at 90% of the 5h window the gateway picks `tier-vibe`. *Test:* table-driven test over synthetic allowance states. *Deps:* 9.
12. **Cross-lab critique profile** — agent-kit. *Why:* the ported workflow needs three labs. *Change:* free reviewers plus Go Flash models from three labs, `gpt-6-luna` opt-in; `distinct_families: 3`. *AC:* a critique run produces three findings sets from three labs. *Test:* fixture asserting distinct families in the resolved model set. *Deps:* 7.
13. **One-command `critique-spec` end to end** — agent-kit. *Why:* the done-criterion workflow. *Change:* single entry point: author Claude Code → reviewers `tier-review` → synthesis by the coordinator. *AC:* one command produces a synthesis plus a ledger line per reviewer. *Test:* end-to-end fixture with stubbed harnesses. *Deps:* 9, 12, **#903**.
14. **Hermes upgrade and tier plugin** — agent-kit. *Why:* 0.19.0 predates the delegation seam. *Change:* pin 0.23.2; add `delegation-litellm-tiers` plugin and the six named providers. *AC:* `hermes delegation config` lists the tiers; an unknown tier is refused. *Test:* plugin unit test on `route_task` with a bad tier. *Deps:* 3, 4.
15. **Hermes overnight cron job** — agent-kit. *Why:* free/Go capacity is idle at night. *Change:* top-level cron one-shot, `--yolo -t file,web`, deny-all repo tools, scheduled outside the DeepSeek 2× windows. *AC:* a job runs, writes a report, and leaves no repo diff. *Test:* dry-run with a stub provider. *Deps:* 14, **#903**.
16. **Confine every CLI client** — agent-kit. *Why:* **#903** blocks any new CLI client. *Change:* enforce working-directory confinement for OpenCode and every harness, not just Pi. *AC:* a delegate attempting to write outside its working dir fails. *Test:* escape-attempt fixture. *Deps:* none — **blocker for 13, 15, 17**.
17. **Pi on non-macOS hosts** — agent-kit. *Why:* **#904** means Pi cannot run on a Mac. *Change:* document and enforce Pi routes on the home server/k3s only; fail fast on macOS. *AC:* `--harness pi-*` on macOS exits with a pointer to #904. *Test:* platform-gated test. *Deps:* **#904**.
18. **Skill updates** — agent-kit. *Why:* the skills still assume static cheapest-first. *Change:* `delegate`, `critique`, `review-*`, `research`, `/tdd`, `/code-review` per the table above. *AC:* each skill names its tier and emits a routing record. *Test:* snapshot test on rendered skill text. *Deps:* 8, 12.

### Open questions for the owner

1. Is PAL MCP clink in the kit? It appears nowhere in the dump — absence of evidence only.
2. Does `opencode.allowance` have a documented schema, or any usage endpoint beyond the Console page? If not, ticket 11 degrades to key-level caps.
3. Does LiteLLM accept a `5h` `budget_duration`, or only `1d`/`30d`?
4. Does `routing_groups` membership accept globs, or must `sync.py` expand them?
5. Which public IP is on which host, and is IP-B reachable from k3s without a tunnel?
6. Are the two Claude Max x5 plans separate accounts (separate logins) or one account with two seats? This decides whether `tier-claude-code` parallelism 2 is real.
7. Is the second Gold Band instance actually running, and is `~/.gold-band/settings.json` under `converge.py` control?
8. What is in `council/`?
9. Is the missing `anthropic` route in `routes.json` intentional, or a registry sync gap?
10. Will the owner accept a Hermes 0.19.0 → 0.23.2 upgrade, given the churn?
11. Are CLI logins on the home server acceptable, or laptop-only?
12. Should Vibe outrank Go in the ladder (Vibe is subsidised, Go draws a shared pool)?

### Facts not verified

- LiteLLM key-scoped model allowlists and access-group semantics beyond what the supplied pages state — **UNVERIFIED**.
- The `callbacks:` registration form and the exact `kwargs` keys available to a custom callback — **UNVERIFIED**.
- Whether `model_id` hash collisions actually share cooldown state — **UNVERIFIED** (the hash is documented; the consequence is inference).
- Whether `order` leaks into a routing group's union — **UNVERIFIED**.
- Whether native provider prefixes ignore `api_base` — **UNVERIFIED**; hence the `openai/` rule.
- Whether LiteLLM honours `HTTPS_PROXY` uniformly across providers — **UNVERIFIED**.
- Cilium `CiliumEgressGatewayPolicy` and MetalLB `externalTrafficPolicy` behaviour for the second IP — **UNVERIFIED**.
- Whether Hermes 0.19.0 has the `DelegationProvider` seam at all — **UNVERIFIED**; check `~/.hermes/hermes-agent/agent/delegation_provider.py`.
- Whether Responses-API models (`gpt-6-luna`, `grok-4.7`) pass through the installed LiteLLM version — **UNVERIFIED**.
- The ToS reading on per-IP limits is interpretation, not legal advice — **UNVERIFIED**.
- `council/` mechanism — **UNVERIFIED**.
- Whether `acpx`/`claude-agent-acp` are maintained — **UNVERIFIED** (they are simply not installed).

## 9. Sources

- the provider pages and public `/models` endpoints named in each section, read 2026-11-18;
- LiteLLM's provider docs (https://docs.litellm.ai/docs/providers) and the `LlmProviders` enum in
  `BerriAI/litellm` main;
- the curated lists:
  - https://github.com/ClawLabsAI/free-ai-models
  - https://github.com/mnfst/awesome-free-llm-apis (also at https://manifest.build/free-models/)
  - https://www.ayautomate.com/free-models
  - https://freellmapi.co
  - https://kilo.ai/landing/free-models
  - a Reddit test report of November 2026

Where a list and a provider disagreed, the provider's page won. Some claims the lists made that
the providers contradict:

- Cerebras has no permanent free tier.
- Groq has dropped Llama from its free plan.
- Mistral has no free API.
- LLM7 prices every model.
- Ollama Cloud has no free plan.
