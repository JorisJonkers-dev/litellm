// The route list is the access boundary: virtual keys are restricted to a route, and each route
// names exactly one provider and one upstream key. Wildcards are deliberate and enumerated here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
// Comments are prose; only the configuration itself is checked.
const CONFIG = readFileSync(`${ROOT}/config/config.yaml`, 'utf8').replace(/^\s*#.*$/gm, '')
const ROUTES = [...CONFIG.matchAll(/^ {2}- model_name: (\S+)$/gm)].map((match) => match[1])
const PROJECT = readFileSync(`${ROOT}/deploy/litellm.project.yml`, 'utf8')
// The proxy Process's own grant, not the catalogue Process's.
const PROXY_SECRETS = PROJECT.slice(PROJECT.indexOf('secrets:'), PROJECT.indexOf('placement:'))

test('every route is a provider wildcard the estate names', () => {
  assert.deepEqual(ROUTES, ['vibe/*', 'vibe-study/*', 'opencode-go/*', 'openrouter/*', 'local/*'])
})

test('each wildcard hands the id to one provider with its own upstream key', () => {
  const lines = CONFIG.split('\n').filter((line) => line.includes('litellm_params'))
  assert.equal(lines.length, ROUTES.length)
  const expected = [
    ['mistral/*', 'VIBE_API_KEY', 'MISTRAL_API_BASE'],
    ['mistral/*', 'VIBE_STUDY_API_KEY', 'MISTRAL_API_BASE'],
    ['openai/*', 'OPENCODE_GO_API_KEY', 'OPENCODE_GO_API_BASE'],
    ['openrouter/*', 'OPENROUTER_API_KEY', 'OPENROUTER_API_BASE'],
    ['openai/*', 'LOCAL_LLM_API_KEY', 'LOCAL_LLM_API_BASE'],
  ]
  lines.forEach((line, i) => {
    assert.match(line, new RegExp(`model: ${expected[i][0].replace('*', '\\*')},`))
    assert.match(line, new RegExp(`api_key: os\\.environ/${expected[i][1]} `))
    assert.match(line, new RegExp(`api_base: os\\.environ/${expected[i][2]},`))
  })
})

test('no Zen route and no catch-all wildcard', () => {
  assert.doesNotMatch(CONFIG, /model_name: "?\*/)
  assert.doesNotMatch(CONFIG, /opencode\.ai\/zen\/v1/)
})

test('upstream keys and bases are environment names, never values', () => {
  for (const [, key] of CONFIG.matchAll(/api_(?:key|base): (\S+?)\s*[},]/g)) assert.match(key, /^os\.environ\/[A-Z_]+$/)
  assert.match(CONFIG, /master_key: os\.environ\/LITELLM_MASTER_KEY/)
})

test('telemetry is metadata only', () => {
  assert.match(CONFIG, /turn_off_message_logging: true/)
  assert.match(CONFIG, /store_prompts_in_spend_logs: false/)
  assert.match(CONFIG, /callbacks: \["prometheus", "otel"\]/)
  // No metric label can carry a person or an address.
  const labels = [...CONFIG.matchAll(/include_labels: \[([^\]]*)\]/g)].flatMap((m) => m[1].split(',').map((l) => l.trim()))
  for (const forbidden of ['user', 'user_email', 'end_user', 'client_ip', 'user_agent', 'team', 'hashed_api_key']) {
    assert.ok(!labels.includes(forbidden), forbidden)
  }
})

test('every env name the config reads is set by the Process env file', () => {
  const env = readFileSync(`${ROOT}/deploy/env/litellm/base.env`, 'utf8')
  for (const [, name] of CONFIG.matchAll(/os\.environ\/([A-Z_]+)/g)) assert.match(env, new RegExp(`^${name}=`, 'm'), name)
})

test('the provider keys the config names are Vault keys the Project grants', () => {
  for (const key of ['vibe_api_key', 'vibe_study_api_key', 'opencode_go_api_key', 'openrouter_api_key']) {
    assert.match(PROXY_SECRETS, new RegExp(`\\b${key}\\b`))
  }
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
  const catalog = PROJECT.slice(PROJECT.indexOf('id: litellm-catalog'))
  assert.doesNotMatch(catalog, /oidc_client_secret/)
})

test('the UI is reached through the one edge-signed-in exposure and metrics stay unrouted', () => {
  assert.equal((PROJECT.match(/^\s+host: /gm) ?? []).length, 1)
  assert.match(PROJECT, /host: litellm\.jorisjonkers\.dev\n\s+audience: authenticated/)
  assert.doesNotMatch(PROJECT, /surface: metrics\s*\}/)
  assert.doesNotMatch(PROJECT, /audience: anonymous/)
})

test('models added through the admin API are stored in the database, wildcards stay as the fallback', () => {
  assert.match(CONFIG, /^ {2}store_model_in_db: true$/m)
  assert.ok(ROUTES.every((route) => route.endsWith('/*')))
})
