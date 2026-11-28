// The route list is the access boundary: virtual keys are restricted to a route, each route names
// exactly one credential, and every deployment names a credential that exists. LiteLLM sends no
// credentials at all when a litellm_credential_name does not resolve, so a dangling name is a
// silently unauthenticated deployment: that check is the point of this file.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
// Comments are prose; only the configuration itself is checked.
const CONFIG = readFileSync(`${ROOT}/config/config.yaml`, 'utf8').replace(/^\s*#.*$/gm, '')
const ROUTES = [...CONFIG.matchAll(/^ {2}- model_name: (\S+)$/gm)].map((match) => match[1])
const WILDCARDS = [...CONFIG.matchAll(/^ {2}- model_name: (\S+)\n {4}litellm_params: \{ model: (\S+), litellm_credential_name: ([^\s,]+)(?:, extra_headers: \{ x-opencode-session: litellm-proxy \})?(?:, max_budget: [\d.]+, budget_duration: \w+)? \}/gm)]
  .map((match) => ({ name: match[1], model: match[2], credential: match[3] }))
const CREDENTIAL_BLOCK = CONFIG.slice(CONFIG.indexOf('credential_list:'), CONFIG.indexOf('model_list:'))
const CREDENTIALS = [...CREDENTIAL_BLOCK.matchAll(/^ {2}- credential_name: (\S+)$/gm)].map((match) => match[1])
const CREDENTIAL_VALUES = Object.fromEntries(
  CREDENTIAL_BLOCK.split(/^ {2}- credential_name: /m).slice(1).map((entry) => [
    entry.slice(0, entry.indexOf('\n')),
    Object.fromEntries([...entry.matchAll(/(\w+): (os\.environ\/[A-Z_]+)/g)].map((match) => [match[1], match[2]])),
  ]),
)
const ROUTER = CONFIG.slice(CONFIG.indexOf('router_settings:'), CONFIG.indexOf('general_settings:'))
// The generated file is a JSON document under a comment header, so the comment lines go first.
const GENERATED = readFileSync(`${ROOT}/config/models.generated.yaml`, 'utf8')
  .split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n')
const DEPLOYMENTS = JSON.parse(GENERATED).model_list
// The second wave (litellm#64): UnoRouter is anonymous, so it has no Vault field.
const WAVE2_ROUTES = [
  'bazaarlink', 'electronhub', 'unorouter', 'cohere', 'airforce', 'plugsky', 'poe', 'vercel', 'watsonx', 'novita', 'together',
  'typhoon', 'sealion', 'gonka', 'longcat', 'neosantara',
]
const WAVE2_FIELDS = {
  bazaarlink: 'bazaarlink_api_key', electronhub: 'electronhub_api_key', cohere: 'cohere_api_key', airforce: 'airforce_api_key',
  plugsky: 'plugsky_api_key', poe: 'poe_api_key', vercel: 'vercel_ai_gateway_api_key', watsonx: 'watsonx_api_key',
  novita: 'novita_api_key', together: 'together_api_key', typhoon: 'typhoon_api_key', sealion: 'sealion_api_key',
  gonka: 'gonka_api_key', longcat: 'longcat_api_key', neosantara: 'neosantara_api_key',
}
// The free-only routes of agent-kit#916 beside openrouter: each has its own credential and access group.
const FREE_ROUTES = [
  'openrouter', 'groq', 'cloudflare', 'nvidia', 'zai', 'kilo', 'ovh', 'requesty', 'axforge', 'naga', 'routeway', 'inferbase',
  ...WAVE2_ROUTES,
]
const PROJECT = readFileSync(`${ROOT}/deploy/litellm.project.yml`, 'utf8')
// The proxy Process's own grant, not the catalogue Process's.
const PROXY_SECRETS = PROJECT.slice(PROJECT.indexOf('secrets:'), PROJECT.indexOf('placement:'))
const CATALOG = PROJECT.slice(PROJECT.indexOf('id: litellm-catalog'))

// A deployment whose credential name is not in credential_list: LiteLLM would send no credentials.
function dangling(deployments) {
  return deployments
    .map((deployment) => deployment?.litellm_params?.litellm_credential_name)
    .filter((name) => !CREDENTIALS.includes(name))
}

test('every route is a flat catch-all wildcard the estate names', () => {
  assert.deepEqual(ROUTES, ['vibe/*', 'vibe-study/*', 'vibe-api/*', 'vibe-study-api/*', 'opencode-go/*', 'local/*'])
  assert.deepEqual(WILDCARDS.map((wildcard) => wildcard.name), ROUTES)
})

test('each wildcard hands the id to one provider through its own credential', () => {
  const expected = {
    'vibe/*': 'mistral/*',
    'vibe-study/*': 'mistral/*',
    'vibe-api/*': 'mistral/*',
    'vibe-study-api/*': 'mistral/*',
    // LiteLLM's own OpenCode Go provider: it picks the API family per model and prices the request.
    'opencode-go/*': 'opencode_go/*',
    'local/*': 'openai/*',
  }
  for (const wildcard of WILDCARDS) {
    assert.equal(wildcard.model, expected[wildcard.name], wildcard.name)
    // The credential is the route, so a key cannot be pointed at another account's base.
    assert.equal(wildcard.credential, wildcard.name.replace('/*', ''), wildcard.name)
  }
})

test('no Zen route, no catch-all wildcard and no paid OpenRouter wildcard', () => {
  assert.doesNotMatch(CONFIG, /model_name: "?\*/)
  assert.doesNotMatch(CONFIG, /opencode\.ai\/zen\/v1/)
  // OpenRouter is free only, and a suffix wildcard is not verified against the pinned release.
  assert.doesNotMatch(CONFIG, /model_name: openrouter\//)
})

test('no free-route deployment is in canonical, each is in its own route group and the free group', () => {
  const groups = Object.fromEntries(FREE_ROUTES.map((route) => [route, [`${route}-free-public`, 'free']]))
  for (const deployment of DEPLOYMENTS) {
    const route = deployment.model_info?.route
    const found = deployment.model_info.access_groups
    if (deployment.model_name.startsWith('google-free/')) {
      // The Google free tier alone: no paid deployment behind it, so a refusal fails.
      assert.deepEqual(found, ['free'], deployment.model_info.id)
      assert.equal(route, 'google', deployment.model_info.id)
      assert.equal(deployment.litellm_params.max_budget, undefined, deployment.model_info.id)
      continue
    }
    if (deployment.model_name.startsWith('free-router/')) {
      // A free router (OpenRouter's openrouter/free): the free group alone, zero cost, no budget.
      assert.deepEqual(found, ['free'], deployment.model_info.id)
      assert.equal(deployment.litellm_params.input_cost_per_token, 0, deployment.model_info.id)
      assert.equal(deployment.litellm_params.output_cost_per_token, 0, deployment.model_info.id)
      assert.equal(deployment.litellm_params.max_budget, undefined, deployment.model_info.id)
      continue
    }
    if (deployment.model_name.startsWith('free/')) continue // the shared free group, checked below
    if (deployment.model_name.startsWith('opencode-go-free/')) continue // its own test below
    if (!(route in groups)) {
      assert.ok(!found.includes('free') && !found.some((group) => group.endsWith('-free-public') && !['opencode-go-free-public', 'google-free-public'].includes(group)),
        deployment.model_info.id)
      continue
    }
    assert.deepEqual(found, groups[route], deployment.model_info.id)
    assert.ok(deployment.model_name.startsWith(`${route}/`), deployment.model_name)
    // Only Inferbase has a budget: its $0.50 a day free share.
    if (route === 'inferbase') assert.equal(deployment.litellm_params.max_budget, 0.5, deployment.model_info.id)
    else assert.equal(deployment.litellm_params.max_budget, undefined, deployment.model_info.id)
  }
  // A model name never mixes access groups: LiteLLM grants a key a name, not a deployment.
  const byName = {}
  for (const deployment of DEPLOYMENTS) {
    byName[deployment.model_name] ??= new Set()
    byName[deployment.model_name].add(JSON.stringify(deployment.model_info.access_groups))
  }
  for (const [name, found] of Object.entries(byName)) assert.equal(found.size, 1, name)
  // A per-route free group is alone in its name. The shared free/<canonical> group holds several free
  // copies, and every one is zero-cost, so there is nothing paid to fall over to.
  const count = {}
  for (const deployment of DEPLOYMENTS) count[deployment.model_name] = (count[deployment.model_name] ?? 0) + 1
  for (const deployment of DEPLOYMENTS) {
    if (!deployment.model_info.access_groups.includes('free')) continue
    if (deployment.model_name.startsWith('free/')) {
      assert.deepEqual(deployment.model_info.access_groups, ['free'], deployment.model_info.id)
      assert.equal(deployment.litellm_params.input_cost_per_token, 0, deployment.model_info.id)
      assert.equal(deployment.litellm_params.output_cost_per_token, 0, deployment.model_info.id)
      assert.equal(deployment.litellm_params.max_budget, undefined, deployment.model_info.id)
    } else assert.equal(count[deployment.model_name], 1, deployment.model_name)
  }
})

test('every anthropic deployment is gated behind the anthropic access group, never canonical', () => {
  const anthropic = DEPLOYMENTS.filter((deployment) => deployment.model_info?.route === 'anthropic')
  for (const deployment of anthropic) {
    assert.deepEqual(deployment.model_info.access_groups, ['anthropic'], deployment.model_info.id)
  }
  for (const deployment of DEPLOYMENTS) {
    if (deployment.model_info?.route !== 'anthropic') {
      assert.ok(!deployment.model_info.access_groups.includes('anthropic'), deployment.model_info.id)
    }
  }
  assert.doesNotMatch(CONFIG, /model_name: anthropic\//)
})

test('every opencode-go -free id is a free/<model> copy alone in the free group, never in a paid group', () => {
  // OpenCode Go serves its -free ids on a paid route: they join free/<model> only (AGENTS.md, "Free groups").
  const free = DEPLOYMENTS.filter((deployment) => deployment.model_info?.route === 'opencode-go' && deployment.litellm_params.model.endsWith('-free'))
  assert.ok(free.length > 0)
  for (const deployment of free) {
    assert.ok(deployment.model_name.startsWith('free/'), deployment.model_name)
    assert.deepEqual(deployment.model_info.access_groups, ['free'], deployment.model_info.id)
    assert.equal(deployment.litellm_params.input_cost_per_token, 0, deployment.model_info.id)
    assert.equal(deployment.litellm_params.output_cost_per_token, 0, deployment.model_info.id)
    assert.equal(deployment.litellm_params.max_budget, undefined, deployment.model_info.id)
  }
  assert.ok(!DEPLOYMENTS.some((deployment) => deployment.model_name.startsWith('opencode-go-free/')))
  assert.ok(!DEPLOYMENTS.some((deployment) => deployment.model_info.access_groups.includes('opencode-go-free-public')))
})

test('no openrouter deployment denies free-model training: the route is public work only', () => {
  for (const deployment of DEPLOYMENTS.filter((d) => d.model_info?.route === 'openrouter')) {
    assert.deepEqual(deployment.litellm_params.extra_body, { provider: { data_collection: 'allow', allow_fallbacks: false } })
  }
})

test('the config includes the generated deployments', () => {
  assert.match(CONFIG, /^include: \[models\.generated\.yaml\]$/m)
})

test('every credential has a name, values and the required credential_info', () => {
  assert.deepEqual(CREDENTIALS, [
    'vibe', 'vibe-study', 'vibe-api', 'vibe-study-api', 'opencode-go', 'openrouter', 'local',
    'groq', 'cloudflare', 'nvidia', 'zai', 'kilo', 'ovh', 'requesty', 'axforge', 'naga', 'routeway', 'inferbase',
    ...WAVE2_ROUTES,
    'google', 'anthropic',
  ])
  assert.equal((CREDENTIAL_BLOCK.match(/^ {4}credential_info: \{\}$/gm) ?? []).length, CREDENTIALS.length)
  assert.deepEqual(Object.keys(CREDENTIAL_VALUES), CREDENTIALS)
})

test('the Mistral accounts carry a key and a base, Google, Anthropic and OpenCode Go a key only', () => {
  assert.deepEqual(CREDENTIAL_VALUES.vibe, { api_key: 'os.environ/VIBE_API_KEY', api_base: 'os.environ/MISTRAL_API_BASE' })
  assert.deepEqual(CREDENTIAL_VALUES['vibe-api'], {
    api_key: 'os.environ/VIBE_API_GENERAL_KEY', api_base: 'os.environ/MISTRAL_API_BASE',
  })
  assert.deepEqual(CREDENTIAL_VALUES['vibe-study-api'], {
    api_key: 'os.environ/VIBE_STUDY_API_GENERAL_KEY', api_base: 'os.environ/MISTRAL_API_BASE',
  })
  assert.deepEqual(CREDENTIAL_VALUES.google, { api_key: 'os.environ/GEMINI_API_KEY' })
  assert.deepEqual(CREDENTIAL_VALUES.anthropic, { api_key: 'os.environ/ANTHROPIC_API_KEY' })
  // A base would send every Go model to one API family; opencode_go picks the base per model.
  assert.deepEqual(CREDENTIAL_VALUES['opencode-go'], { api_key: 'os.environ/OPENCODE_GO_API_KEY' })
  for (const route of ['vibe-study', 'openrouter', 'local']) {
    assert.ok(CREDENTIAL_VALUES[route].api_key && CREDENTIAL_VALUES[route].api_base, route)
  }
})

test('every free-only route has a credential and no wildcard, and a key is a Vault field', () => {
  for (const route of FREE_ROUTES) {
    assert.ok(CREDENTIALS.includes(route), route)
    assert.ok(!ROUTES.some((name) => name.startsWith(`${route}/`)), route)
  }
  const fields = { groq: 'groq_api_key', cloudflare: 'cloudflare_api_key', nvidia: 'nvidia_nim_api_key', zai: 'zai_api_key',
    requesty: 'requesty_api_key', axforge: 'axforge_api_key', naga: 'naga_api_key', routeway: 'routeway_api_key',
    inferbase: 'inferbase_api_key', ...WAVE2_FIELDS }
  for (const field of Object.values(fields)) {
    assert.match(PROXY_SECRETS, new RegExp(`\\b${field}\\b`), field)
    assert.match(CATALOG, new RegExp(`\\b${field}\\b`), field)
  }
  // Each is an optional key in both grants: a Process starts whether the Vault field is there or not.
  for (const field of Object.values(fields)) {
    for (const [scope, text] of [['proxy', PROXY_SECRETS], ['catalog', CATALOG]]) {
      assert.match(text, new RegExp(`- name: ${field}\\n +optional: true\\n`), `${scope} ${field}`)
    }
  }
  // The keys every route needs stay plain names, so a missing one still stops the Process.
  for (const field of ['master_key', 'salt_key', 'vibe_api_key', 'openrouter_api_key', 'anthropic_api_key', 'local_llm_api_key']) {
    assert.match(PROXY_SECRETS, new RegExp(`^ +- ${field}$`, 'm'), field)
  }
  // The anonymous routes carry a placeholder key, not a Vault field.
  assert.deepEqual(CREDENTIAL_VALUES.kilo.api_key, 'os.environ/ANONYMOUS_UPSTREAM_KEY')
  assert.deepEqual(CREDENTIAL_VALUES.ovh.api_key, 'os.environ/ANONYMOUS_UPSTREAM_KEY')
  assert.deepEqual(CREDENTIAL_VALUES.unorouter.api_key, 'os.environ/ANONYMOUS_UPSTREAM_KEY')
})

test('the second-wave routes: optional Vault fields, a base only where the spec names one, free-only deployments', () => {
  for (const [route, field] of Object.entries(WAVE2_FIELDS)) {
    for (const file of ['litellm', 'litellm-catalog']) {
      const env = readFileSync(`${ROOT}/deploy/env/${file}/base.env`, 'utf8')
      assert.match(env, new RegExp(`^${field.replace(/_api_key$/, '').toUpperCase()}_API_KEY=\\$\\{secret:secret/data/platform/litellm#${field}\\}$`, 'm'), `${file} ${route}`)
    }
  }
  assert.equal(Object.keys(WAVE2_FIELDS).length, WAVE2_ROUTES.length - 1) // every route but UnoRouter has a key
  // Settings that are not secrets are optional Vault fields too, mapped by the env files; no placeholder is left.
  const SETTINGS = { cloudflare_account_id: 'CLOUDFLARE_ACCOUNT_ID', bazaarlink_api_base: 'BAZAARLINK_API_BASE', cohere_api_base: 'COHERE_LIST_BASE',
    watsonx_api_base: 'WATSONX_API_BASE', watsonx_project_id: 'WATSONX_PROJECT_ID' }
  for (const file of ['litellm', 'litellm-catalog']) {
    const env = readFileSync(`${ROOT}/deploy/env/${file}/base.env`, 'utf8')
    assert.doesNotMatch(env, /REPLACE_WITH/, file)
    for (const [field, name] of Object.entries(SETTINGS)) {
      if (file === 'litellm' && field === 'cohere_api_base') continue // the sync's list base is the catalogue Process's alone
      assert.match(env, new RegExp(`^${name}=\\$\\{secret:secret/data/platform/litellm#${field}\\}$`, 'm'), `${file} ${name}`)
    }
    assert.doesNotMatch(env, /^COHERE_API_BASE=/m, file) // LiteLLM's cohere_chat would read it
    assert.match(env, /^WATSONX_URL=\$\{secret:secret\/data\/platform\/litellm#watsonx_api_base\}$/m, file)
  }
  assert.doesNotMatch(PROXY_SECRETS, /cohere_api_base/) // the sync's list base is the catalogue Process's alone
  for (const field of Object.keys(SETTINGS)) {
    for (const [scope, text] of [['proxy', PROXY_SECRETS], ['catalog', CATALOG]]) {
      if (scope === 'proxy' && field === 'cohere_api_base') continue
      assert.match(text, new RegExp(`- name: ${field}\\n +optional: true\\n`), `${scope} ${field}`)
    }
  }
  for (const route of ['cohere', 'watsonx']) assert.deepEqual(Object.keys(CREDENTIAL_VALUES[route]), ['api_key'], route)
})

test('every deployment names a credential that exists', () => {
  assert.ok(Array.isArray(DEPLOYMENTS))
  assert.ok(DEPLOYMENTS.every((deployment) => deployment.litellm_params?.litellm_credential_name))
  assert.deepEqual(dangling(DEPLOYMENTS), [])
  // The guard itself: a name that is not in credential_list must fail here, not at the first request.
  assert.deepEqual(dangling([{ litellm_params: { litellm_credential_name: 'vibe-paid' } }]), ['vibe-paid'])
  assert.deepEqual(dangling([{ litellm_params: {} }]), [undefined])
})

test('upstream keys and bases are environment names, never values', () => {
  for (const [, value] of CONFIG.matchAll(/api_(?:key|base): (\S+?)\s*[},]/g)) assert.match(value, /^os\.environ\/[A-Z_]+$/)
  assert.match(CONFIG, /master_key: os\.environ\/LITELLM_MASTER_KEY/)
})

test('telemetry is metadata only', () => {
  assert.match(CONFIG, /turn_off_message_logging: true/)
  assert.match(CONFIG, /store_prompts_in_spend_logs: false/)
  assert.match(CONFIG, /callbacks: \["prometheus", "otel", "tier_telemetry\.tier_telemetry", "account_budget\.account_budget", "free_limits\.free_limits"\]/)
  // The callback module is named relative to the config, so the image carries it beside it.
  assert.match(readFileSync(`${ROOT}/Dockerfile`, 'utf8'), /^COPY config\/tier_telemetry\.py \/app\/tier_telemetry\.py$/m)
  // No metric label can carry a person or an address.
  const labels = [...CONFIG.matchAll(/include_labels: \[([^\]]*)\]/g)].flatMap((m) => m[1].split(',').map((l) => l.trim()))
  for (const forbidden of ['user', 'user_email', 'end_user', 'client_ip', 'user_agent', 'team', 'hashed_api_key', 'api_base']) {
    assert.ok(!labels.includes(forbidden), forbidden)
  }
})

test('the serving deployment is visible per request, and no label names a person', () => {
  const groups = Object.fromEntries(
    [...CONFIG.matchAll(/- group: (\S+)\n\s+metrics: \[[^\]]*\]\n\s+include_labels: \[([^\]]*)\]/g)]
      .map((match) => [match[1], match[2].split(',').map((label) => label.trim())]),
  )
  for (const group of ['requests', 'errors']) {
    assert.ok(groups[group]?.includes('model_id'), group)
    assert.ok(groups[group]?.includes('api_provider'), group)
  }
  const labels = Object.values(groups).flat()
  for (const forbidden of ['user', 'user_email', 'end_user', 'client_ip', 'user_agent', 'team', 'hashed_api_key']) {
    assert.ok(!labels.includes(forbidden), forbidden)
  }
})

test('budgets are exported for keys and for the route teams that hold them', () => {
  const groups = Object.fromEntries(
    [...CONFIG.matchAll(/- group: (\S+)\n\s+metrics: \[([^\]]*)\]\n\s+include_labels: \[([^\]]*)\]/g)]
      .map((match) => [match[1], { metrics: match[2].split(',').map((m) => m.trim()), labels: match[3].split(',').map((l) => l.trim()) }]),
  )
  assert.deepEqual(groups.budgets?.labels, ['api_key_alias'])
  assert.ok(groups.budgets.metrics.includes('litellm_api_key_budget_remaining_hours_metric'))
  assert.deepEqual(groups['team-budgets']?.labels, ['team_alias'])
  for (const metric of ['litellm_remaining_team_budget_metric', 'litellm_team_max_budget_metric', 'litellm_team_budget_remaining_hours_metric']) {
    assert.ok(groups['team-budgets'].metrics.includes(metric), metric)
  }
})

test('the router falls through a cooled-down or spent deployment, per deployment', () => {
  assert.match(CONFIG, /^router_settings:$/m)
  assert.match(ROUTER, /optional_pre_call_checks: \[router_budget_limiting, enforce_model_rate_limits\]/)
  assert.match(ROUTER, /^ {2}num_retries: 2$/m)
  // The cooldown and the allowed-fails policies are per deployment, in the generated file.
  assert.doesNotMatch(ROUTER, /allowed_fails|cooldown_time/)
})

test('the local route calls the GPU workstation gate with the key only this project holds', () => {
  for (const file of ['litellm', 'litellm-catalog']) {
    const env = readFileSync(`${ROOT}/deploy/env/${file}/base.env`, 'utf8').replace(/^\s*#.*$/gm, '')
    assert.match(env, /^LOCAL_LLM_API_BASE=http:\/\/100\.112\.121\.102:11435\/v1$/m, file)
    assert.match(env, /^LOCAL_LLM_API_KEY=\$\{secret:secret\/data\/platform\/litellm#local_llm_api_key\}$/m, file)
  }
  // The proxy and both catalogue Processes (scheduled and on release) read the key, so each grant names it.
  assert.equal((PROJECT.match(/^ +- local_llm_api_key$/gm) ?? []).length, 3)
})

test('no model is stored in the proxy database', () => {
  assert.doesNotMatch(CONFIG, /store_model_in_db/)
})

test('every env name the config reads is set by the proxy env file', () => {
  const env = readFileSync(`${ROOT}/deploy/env/litellm/base.env`, 'utf8')
  for (const [, name] of CONFIG.matchAll(/os\.environ\/([A-Z_]+)/g)) assert.match(env, new RegExp(`^${name}=`, 'm'), name)
})

test('the catalogue env carries every provider key and base the config names, and no master key', () => {
  const catalog = readFileSync(`${ROOT}/deploy/env/litellm-catalog/base.env`, 'utf8')
  for (const [, name] of CONFIG.matchAll(/os\.environ\/([A-Z_]+)/g)) {
    if (name === 'LITELLM_MASTER_KEY') continue
    assert.match(catalog, new RegExp(`^${name}=`, 'm'), name)
  }
  // The sync lists and probes at the providers and opens a pull request; it never calls the proxy.
  assert.doesNotMatch(catalog, /^LITELLM_MASTER_KEY=/m)
  assert.doesNotMatch(catalog, /^LITELLM_(?:HOST|PORT)=/m)
  for (const name of ['MODELS_REPO', 'MODELS_PATH', 'MODELS_BRANCH', 'MODELS_TOKEN']) {
    assert.match(catalog, new RegExp(`^${name}=`, 'm'), name)
  }
  assert.match(catalog, /^MODELS_REPO=JorisJonkers-dev\/litellm$/m)
  assert.match(catalog, /^MODELS_PATH=config\/models\.generated\.yaml$/m)
  assert.match(catalog, /^MODELS_BRANCH=bot\/model-catalog$/m)
  assert.match(catalog, /^MODELS_TOKEN=\$\{secret:secret\/data\/platform\/litellm#catalog_pr_token\}$/m)
})

test('the routing rules are the decided policy', () => {
  const rules = JSON.parse(readFileSync(`${ROOT}/catalog/routing.json`, 'utf8'))
  assert.deepEqual(rules.tiers, [
    [['local', 1]],
    [['google', 1], ['anthropic', 1]],
    [['vibe', 2], ['vibe-study', 1]],
    [['opencode-go', 1]],
    [['vibe-api', 2], ['vibe-study-api', 1]],
  ])
  // The general Mistral API keys are the last resort: a model any other route serves never reaches them.
  const others = ['local', 'google', 'anthropic', 'vibe', 'vibe-study', 'opencode-go']
  assert.deepEqual(rules.backup_only, {
    'opencode-go': ['vibe', 'vibe-study', 'anthropic'],
    'vibe-api': others,
    'vibe-study-api': others,
  })
  assert.deepEqual(rules.exclusive, [{ match: '^claude', routes: ['anthropic'] }])
  // Free deployments are never in a canonical group, so there is no free-first rule.
  assert.equal(rules.free_first_unless_served_by, undefined)
  assert.deepEqual(rules.api_only_routes, ['vibe-api', 'vibe-study-api'])
  assert.deepEqual(rules.public_routes, ['google'])
  assert.equal(rules.limits, undefined)
})

test('every routed account has a safety-net budget and a cooldown', () => {
  const rules = JSON.parse(readFileSync(`${ROOT}/catalog/routing.json`, 'utf8'))
  assert.equal(rules.budgets, undefined)
  assert.deepEqual(rules.accounts, {
    vibe: { routes: ['vibe'], max_budget: 255, window: { calendar: 'month' } },
    'vibe-study': { routes: ['vibe-study'], max_budget: 127, window: { calendar: 'month' } },
    'vibe-api': { routes: ['vibe-api'], max_budget: 25.5, window: { calendar: 'month' } },
    'vibe-study-api': { routes: ['vibe-study-api'], max_budget: 12.7, window: { calendar: 'month' } },
    anthropic: { routes: ['anthropic'], max_budget: 90, window: { days: 30 } },
    'opencode-go': { routes: ['opencode-go'], max_budget: 40, window: { days: 30, anchor: '2026-11-04T10:41:00Z' } },
  })
  // Inferbase has no payment method (owner, 2026-11-26): a free route, rate limited, with no cap to keep.
  assert.ok(!Object.values(rules.accounts).some((account) => account.routes.includes('inferbase')))
  // Google is a free tier: a budget would only take it out of the pick.
  assert.ok(!Object.values(rules.accounts).some((account) => account.routes.includes('google')))
  assert.deepEqual(rules.cooldowns, {
    vibe: 3600,
    'vibe-study': 3600,
    'vibe-api': 3600,
    'vibe-study-api': 3600,
    'opencode-go': 900,
    google: 120,
    anthropic: 300,
    openrouter: 120,
    groq: 120,
    cloudflare: 120,
    nvidia: 120,
    zai: 120,
    kilo: 120,
    ovh: 120,
    requesty: 3600,
    axforge: 120,
    naga: 3600,
    routeway: 3600,
    inferbase: 120,
    // Second wave: 3600 for a daily cap (BazaarLink, Cohere, Api.Airforce, Plugsky), 120 otherwise, 300 for the three routes whose errors were mostly transient (Electron Hub, UnoRouter, Neosantara).
    bazaarlink: 3600, electronhub: 300, unorouter: 300, cohere: 3600, airforce: 3600, plugsky: 3600, poe: 120, vercel: 120,
    watsonx: 120, novita: 120, together: 120, typhoon: 120, sealion: 120, gonka: 120, longcat: 120, neosantara: 300,
  })
  for (const [route, seconds] of Object.entries(rules.cooldowns)) {
    assert.ok(Number.isInteger(seconds) && seconds > 0, route)
  }
})

test('an alias points at a canonical name, never at a vendor path', () => {
  const rules = JSON.parse(readFileSync(`${ROOT}/catalog/routing.json`, 'utf8'))
  for (const [from, to] of Object.entries(rules.aliases)) {
    assert.ok(!to.includes('/') && !to.includes(':'), `${from} -> ${to}`)
    assert.notEqual(from, to)
  }
})

test('the image carries the routing rules and the generated deployments', () => {
  const dockerfile = readFileSync(`${ROOT}/Dockerfile`, 'utf8')
  assert.match(dockerfile, /^COPY .*catalog\/routing\.json/m)
  assert.match(dockerfile, /^COPY config\/models\.generated\.yaml \/app\/models\.generated\.yaml$/m)
})

test('the provider keys the config names are Vault keys the Project grants', () => {
  for (const key of ['vibe_api_key', 'vibe_study_api_key', 'vibe_api_general_key', 'vibe_study_api_general_key',
    'opencode_go_api_key', 'openrouter_api_key', 'google_api_key', 'anthropic_api_key']) {
    assert.match(PROXY_SECRETS, new RegExp(`\\b${key}\\b`))
  }
  // The pull-request token is the catalogue Process's alone.
  assert.doesNotMatch(PROXY_SECRETS, /catalog_pr_token/)
})

test('the catalogue Process needs no proxy and no master key', () => {
  assert.match(CATALOG, /catalog_pr_token/)
  assert.doesNotMatch(CATALOG, /master_key/)
  // Its one edge is the estate Prometheus, for the real-traffic series; never the proxy.
  assert.match(CATALOG, /dependsOn:\n\s+- \{ application: prometheus, surface: http \}\n/)
  // Each of its two Processes (scheduled and on release) declares that edge.
  assert.equal([...CATALOG.matchAll(/application: (\S+?),/g)].map((m) => m[1]).join(), 'prometheus,prometheus')
})

test('the on-release job Process is the scheduled catalogue Process, run once per release', () => {
  const start = CATALOG.indexOf('      - name: litellm-catalog\n')
  const job = CATALOG.indexOf('      - name: litellm-catalog-on-release\n')
  assert.ok(start >= 0 && job > start)
  const scheduled = CATALOG.slice(start, job).replace(/^ *#.*\n/gm, '')
  const onRelease = CATALOG.slice(job).replace(/^ *#.*\n/gm, '')
  assert.match(onRelease, /^ {8}lifecycle: job$/m)
  assert.match(onRelease, /^ {8}startupBudget: 900s/m)
  assert.doesNotMatch(onRelease, /^ {8}(schedule|probes|provides|replicas|cutover):/m)
  // Everything else (image, runtime, command, egress, edges, secrets, placement) is the scheduled Process's.
  const rest = (text) => text.replace(/^ {6}- name: .*\n/m, '').replace(/^ {8}lifecycle: .*\n/m, '')
    .replace(/^ {8}schedule: .*\n/m, '').replace(/^ {8}startupBudget: .*\n/m, '').trimEnd()
  assert.equal(rest(onRelease), rest(scheduled))
})

test('the Project declares the scrape, the dashboard and the catalogue sync', () => {
  assert.match(PROJECT, /scrape: \{ process: litellm, surface: metrics, path: \/metrics\/ \}/)
  assert.match(PROJECT, /from: dashboards\/litellm\.json/)
  assert.match(PROJECT, /lifecycle: scheduled/)
  assert.match(PROJECT, /runtime: python/)
  // The metrics surface is never routed: only the api exposure exists.
  assert.doesNotMatch(PROJECT, /process: litellm, surface: metrics }/)
})

test('the dashboard has a uid and queries only metrics the config enables', () => {
  const dash = JSON.parse(readFileSync(`${ROOT}/deploy/dashboards/litellm.json`, 'utf8'))
  assert.equal(dash.uid, 'litellm')
  // The prometheus callback's series are named in the config; the tier telemetry callback's own in its module.
  const enabled = new Set([...CONFIG.match(/litellm_\w+/g), ...readFileSync(`${ROOT}/config/tier_telemetry.py`, 'utf8').match(/^METRIC = "(litellm_\w+)"/m).slice(1)])
  const used = new Set(JSON.stringify(dash).match(/litellm_\w+/g))
  for (const metric of used) {
    const base = metric.replace(/_total$|_bucket$/, '')
    assert.ok(enabled.has(base), metric)
  }
})

test('the base image tag names the LiteLLM version the images lock records', () => {
  const dockerfile = readFileSync(`${ROOT}/Dockerfile`, 'utf8')
  // The shared build reads the last stage's base, so the test does too.
  const from = [...dockerfile.matchAll(/^FROM (\S+)/gm)].at(-1)?.[1] ?? ''
  assert.match(from, /@sha256:[0-9a-f]{64}$/)
  assert.match(from.split('@')[0].split('/').pop(), /:v?\d+(\.\d+)+([._+-][0-9A-Za-z._+-]+)?$/)
})

test('the admin UI is on and signs in through auth-api OIDC only', () => {
  const env = readFileSync(`${ROOT}/deploy/env/litellm/base.env`, 'utf8').replace(/^\s*#.*$/gm, '')
  assert.doesNotMatch(env, /DISABLE_ADMIN_UI|UI_USERNAME|UI_PASSWORD/)
  assert.match(env, /^GENERIC_CLIENT_ID=litellm$/m)
  assert.match(env, /^GENERIC_CLIENT_SECRET=\$\{secret:secret\/data\/platform\/litellm#oidc_client_secret\}$/m)
  for (const name of ['AUTHORIZATION', 'TOKEN', 'USERINFO']) {
    assert.match(env, new RegExp(`^GENERIC_${name}_ENDPOINT=https://auth\\.jorisjonkers\\.dev/api/`, 'm'))
  }
  assert.match(env, /^PROXY_BASE_URL=https:\/\/litellm\.jorisjonkers\.dev$/m)
  assert.match(env, /^GENERIC_CLIENT_USE_PKCE=true$/m)
  // The immutable subject identifies a user, never the renameable username.
  assert.match(env, /^GENERIC_USER_ID_ATTRIBUTE=sub$/m)
  assert.doesNotMatch(env, /^GENERIC_CLIENT_STATE=/m)
  assert.match(PROXY_SECRETS.replace(/^\s*#.*$/gm, ''), /^ +- oidc_client_secret$/m)
})

test('only ROLE_ADMIN is a proxy admin, everyone else is a viewer the UI refuses', () => {
  const env = readFileSync(`${ROOT}/deploy/env/litellm/base.env`, 'utf8').replace(/^\s*#.*$/gm, '')
  assert.match(env, /^GENERIC_INCLUDE_TOKEN_CLAIMS=true$/m)
  assert.match(env, /^GENERIC_ROLE_MAPPINGS_GROUP_CLAIM=roles$/m)
  assert.match(env, /^GENERIC_ROLE_MAPPINGS_ROLES=\{'proxy_admin':\['ROLE_ADMIN'\]\}$/m)
  assert.match(env, /^GENERIC_ROLE_MAPPINGS_DEFAULT_ROLE=internal_user_viewer$/m)
  assert.match(CONFIG, /ui_access_mode: admin_only/)
})

test('password login, and the master key as its fallback password, are off', () => {
  assert.match(CONFIG, /disable_env_credential_login: true/)
})

test('the UI password is the proxy Process\'s alone, never the catalogue sync\'s', () => {
  assert.doesNotMatch(CATALOG, /oidc_client_secret/)
})

test('the one exposure is anonymous at the edge, so a LiteLLM key works as a plain bearer, and metrics stay unrouted', () => {
  assert.equal((PROJECT.match(/^\s+host: /gm) ?? []).length, 1)
  assert.match(PROJECT, /host: litellm\.jorisjonkers\.dev\n\s+audience: anonymous/)
  assert.doesNotMatch(PROJECT, /audience: authenticated/)
  assert.doesNotMatch(PROJECT, /surface:\s*metrics\s*\}/)
  // Forward-auth would read the bearer as its own token: the key header setting is gone.
  assert.doesNotMatch(CONFIG, /^\s*litellm_key_header_name:/m)
})

test('the UI redirects to the SSO sign-in and the API docs are off', () => {
  const env = readFileSync(`${ROOT}/deploy/env/litellm/base.env`, 'utf8').replace(/^\s*#.*$/gm, '')
  assert.match(env, /^AUTO_REDIRECT_UI_LOGIN_TO_SSO=true$/m)
  assert.match(env, /^NO_DOCS=true$/m)
  assert.match(env, /^NO_REDOC=true$/m)
  assert.match(env, /^NO_OPENAPI=true$/m)
  assert.match(CONFIG, /disable_env_credential_login: true/)
})

test('the tier names are router aliases with fallbacks, and there is no default_fallbacks', () => {
  const live = CONFIG.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n')
  assert.doesNotMatch(live, /default_fallbacks/)
  for (const tier of ['tier-free', 'tier-vibe', 'tier-go-cheap', 'tier-strong-open', 'tier-review']) {
    assert.match(live, new RegExp(`^    "${tier}": "`, 'm'), tier)
    assert.match(live, new RegExp(`^    - \\{"${tier}": \\[`, 'm'), tier)
  }
  // No fallbacks entry starts from a free/* group: nothing reachable from one ends at a paid deployment.
  assert.doesNotMatch(live, /^\s+- \{"free\//m)
})
