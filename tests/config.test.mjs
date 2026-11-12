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
const WILDCARDS = [...CONFIG.matchAll(/^ {2}- model_name: (\S+)\n {4}litellm_params: \{ model: (\S+), litellm_credential_name: (\S+) \}/gm)]
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
    'opencode-go/*': 'openai/*',
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

test('the config includes the generated deployments', () => {
  assert.match(CONFIG, /^include: \[models\.generated\.yaml\]$/m)
})

test('every credential has a name, values and the required credential_info', () => {
  assert.deepEqual(CREDENTIALS, [
    'vibe', 'vibe-study', 'vibe-api', 'vibe-study-api', 'opencode-go', 'openrouter', 'local', 'google', 'anthropic',
  ])
  assert.equal((CREDENTIAL_BLOCK.match(/^ {4}credential_info: \{\}$/gm) ?? []).length, CREDENTIALS.length)
  assert.deepEqual(Object.keys(CREDENTIAL_VALUES), CREDENTIALS)
})

test('the Mistral accounts carry a key and a base, Google and Anthropic a key only', () => {
  assert.deepEqual(CREDENTIAL_VALUES.vibe, { api_key: 'os.environ/VIBE_API_KEY', api_base: 'os.environ/MISTRAL_API_BASE' })
  assert.deepEqual(CREDENTIAL_VALUES['vibe-api'], {
    api_key: 'os.environ/VIBE_API_GENERAL_KEY', api_base: 'os.environ/MISTRAL_API_BASE',
  })
  assert.deepEqual(CREDENTIAL_VALUES['vibe-study-api'], {
    api_key: 'os.environ/VIBE_STUDY_API_GENERAL_KEY', api_base: 'os.environ/MISTRAL_API_BASE',
  })
  assert.deepEqual(CREDENTIAL_VALUES.google, { api_key: 'os.environ/GEMINI_API_KEY' })
  assert.deepEqual(CREDENTIAL_VALUES.anthropic, { api_key: 'os.environ/ANTHROPIC_API_KEY' })
  for (const route of ['vibe-study', 'opencode-go', 'openrouter', 'local']) {
    assert.ok(CREDENTIAL_VALUES[route].api_key && CREDENTIAL_VALUES[route].api_base, route)
  }
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
  assert.match(CONFIG, /callbacks: \["prometheus", "otel"\]/)
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

test('the router falls through a cooled-down or spent deployment, per deployment', () => {
  assert.match(CONFIG, /^router_settings:$/m)
  assert.match(ROUTER, /optional_pre_call_checks: \[router_budget_limiting\]/)
  assert.match(ROUTER, /^ {2}num_retries: 2$/m)
  // The cooldown and the allowed-fails policies are per deployment, in the generated file.
  assert.doesNotMatch(ROUTER, /allowed_fails|cooldown_time/)
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
    [['vibe-api', 2], ['vibe-study-api', 1]],
    [['opencode-go', 1]],
  ])
  assert.deepEqual(rules.backup_only, { 'opencode-go': ['vibe', 'vibe-study', 'vibe-api', 'vibe-study-api', 'anthropic'] })
  assert.deepEqual(rules.exclusive, [{ match: '^claude', routes: ['anthropic'] }])
  assert.deepEqual(rules.free_first_unless_served_by, ['vibe', 'vibe-study', 'vibe-api', 'vibe-study-api', 'anthropic'])
  assert.deepEqual(rules.api_only_routes, ['vibe-api', 'vibe-study-api'])
  assert.deepEqual(rules.public_routes, ['google'])
  assert.equal(rules.limits, undefined)
})

test('every routed account has a safety-net budget and a cooldown', () => {
  const rules = JSON.parse(readFileSync(`${ROOT}/catalog/routing.json`, 'utf8'))
  assert.deepEqual(rules.budgets, {
    vibe: { max_budget: 255, budget_duration: '30d' },
    'vibe-study': { max_budget: 127, budget_duration: '30d' },
    'vibe-api': { max_budget: 25, budget_duration: '30d' },
    'vibe-study-api': { max_budget: 12, budget_duration: '30d' },
    anthropic: { max_budget: 90, budget_duration: '30d' },
    'opencode-go': { max_budget: 15, budget_duration: '30d' },
  })
  // Google is a free tier: a budget would only take it out of the pick.
  assert.equal(rules.budgets.google, undefined)
  assert.deepEqual(rules.cooldowns, {
    vibe: 3600, 'vibe-study': 3600, 'vibe-api': 3600, 'vibe-study-api': 3600, 'opencode-go': 900, google: 120, anthropic: 300,
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
  assert.doesNotMatch(CATALOG, /dependsOn/)
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
  const enabled = new Set(CONFIG.match(/litellm_\w+/g))
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
  assert.match(PROXY_SECRETS.replace(/^\s*#.*$/gm, ''), /keys: \[[^\]]*\boidc_client_secret\b/)
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

test('the UI is reached through the one edge-signed-in exposure and metrics stay unrouted', () => {
  assert.equal((PROJECT.match(/^\s+host: /gm) ?? []).length, 1)
  assert.match(PROJECT, /host: litellm\.jorisjonkers\.dev\n\s+audience: authenticated/)
  assert.doesNotMatch(PROJECT, /surface: metrics\s*\}/)
  assert.doesNotMatch(PROJECT, /audience: anonymous/)
})
