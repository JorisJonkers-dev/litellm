// The published image is LiteLLM with the estate's routes built in. These tests build the image,
// start it the way deploy/litellm.project.yml runs it (read-only root, only its writable paths
// writable, no database) with every upstream pointed at a stub on this host, and talk to it over
// HTTP. IMAGE skips the build and tests an image that already exists.
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const IMAGE = process.env.IMAGE || 'litellm-image-test:local'
const NAME = `litellm-image-test-${process.pid}`
const PROJECT = readFileSync(`${ROOT}/deploy/litellm.project.yml`, 'utf8')
const WRITABLE = PROJECT.match(/writablePaths: \[([^\]]*)\]/)[1].split(',').map((path) => path.trim())
const MASTER = 'sk-image-test-master'
const PROMPT = 'PROMPT-TEXT-MUST-NOT-BE-LOGGED'
const ANSWER = 'ANSWER-TEXT-MUST-NOT-BE-LOGGED'
// route -> the upstream key it must be called with, and the stub standing in for its provider.
const ROUTES = {
  vibe: { key: 'upstream-vibe', env: 'MISTRAL_API_BASE' },
  'vibe-study': { key: 'upstream-vibe-study', env: 'MISTRAL_API_BASE' },
  'opencode-go': { key: 'upstream-opencode-go', env: 'OPENCODE_GO_API_BASE' },
  openrouter: { key: 'upstream-openrouter', env: 'OPENROUTER_API_BASE' },
  local: { key: 'none', env: 'LOCAL_LLM_API_BASE' },
}
const stubs = {} // env name -> { server, port, seen: [] }
const spans = [] // request bodies the OTLP receiver got
let base
let metrics
let otlpPort

function docker(...args) {
  return execFileSync('docker', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
}

function listen(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler)
    server.listen(0, '0.0.0.0', () => resolve({ server, port: server.address().port }))
  })
}

async function startStub(envName) {
  const seen = []
  const stub = await listen((req, res) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      const body = raw ? JSON.parse(raw) : {}
      seen.push({ method: req.method, path: req.url, auth: req.headers.authorization, model: body.model })
      res.setHeader('content-type', 'application/json')
      if (req.method === 'GET') return res.end(JSON.stringify({ object: 'list', data: [{ id: 'listed-one' }, { id: 'listed-two' }] }))
      res.end(JSON.stringify({
        id: 'stub', object: 'chat.completion', created: 1, model: body.model,
        choices: [{ index: 0, message: { role: 'assistant', content: ANSWER }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
      }))
    })
  })
  stubs[envName] = { ...stub, seen }
}

async function waitForLive(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      if ((await fetch(`${url}/health/liveliness`)).status === 200) return
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  throw new Error(`LiteLLM was not live within ${timeoutMs} ms:\n${docker('logs', NAME)}`)
}

function hostPort(port) {
  return `http://127.0.0.1:${docker('port', NAME, `${port}/tcp`).split('\n')[0].split(':').pop()}`
}

async function chat(model) {
  const res = await fetch(`${base}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'x-litellm-api-key': `Bearer ${MASTER}`, 'content-type': 'application/json' },
    body: JSON.stringify({ model, messages: [{ role: 'user', content: PROMPT }] }),
  })
  assert.equal(res.status, 200, await res.clone().text())
  return res.json()
}

before(async () => {
  for (const envName of new Set(Object.values(ROUTES).map((route) => route.env))) await startStub(envName)
  const otlp = await listen((req, res) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => {
      spans.push(Buffer.concat(chunks).toString('latin1'))
      res.statusCode = 200
      res.end()
    })
  })
  otlpPort = otlp.port
  stubs.otlp = otlp
  if (!process.env.IMAGE) docker('build', '-t', IMAGE, '.')
  const upstream = (envName) => `http://host.docker.internal:${stubs[envName].port}/v1`
  docker(
    'run', '-d', '--name', NAME, '--add-host', 'host.docker.internal:host-gateway',
    '-p', '127.0.0.1::4000', '-p', '127.0.0.1::4001', '--read-only',
    ...WRITABLE.flatMap((path) => ['--tmpfs', path]),
    '-e', `LITELLM_MASTER_KEY=${MASTER}`, '-e', 'LITELLM_SALT_KEY=sk-image-test-salt',
    '-e', 'VIBE_API_KEY=upstream-vibe', '-e', 'VIBE_STUDY_API_KEY=upstream-vibe-study',
    '-e', 'OPENCODE_GO_API_KEY=upstream-opencode-go', '-e', 'OPENROUTER_API_KEY=upstream-openrouter',
    '-e', 'LOCAL_LLM_API_KEY=none',
    '-e', `MISTRAL_API_BASE=${upstream('MISTRAL_API_BASE')}`, '-e', `OPENCODE_GO_API_BASE=${upstream('OPENCODE_GO_API_BASE')}`,
    '-e', `OPENROUTER_API_BASE=${upstream('OPENROUTER_API_BASE')}`, '-e', `LOCAL_LLM_API_BASE=${upstream('LOCAL_LLM_API_BASE')}`,
    // The runtime profile hands the collector's gRPC endpoint in production; the test receives HTTP.
    '-e', `OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:${otlpPort}`,
    '-e', 'OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf', '-e', 'OTEL_SERVICE_NAME=litellm',
    '-e', 'LITELLM_TELEMETRY=False', '-e', 'LITELLM_MODE=PRODUCTION', '-e', 'DISABLE_ADMIN_UI=True',
    IMAGE,
  )
  base = hostPort(4000)
  metrics = hostPort(4001)
  await waitForLive(base, 240_000)
})

after(() => {
  try {
    docker('rm', '-f', NAME)
  } catch {
    // already gone
  }
  for (const stub of Object.values(stubs)) stub.server.close()
})

test('the models list is for the master key only, and lists what the live providers offer', async () => {
  const denied = await fetch(`${base}/v1/models`)
  assert.equal(denied.status, 401)
  const res = await fetch(`${base}/v1/models`, { headers: { 'x-litellm-api-key': `Bearer ${MASTER}` } })
  assert.equal(res.status, 200)
  const ids = (await res.json()).data.map((model) => model.id)
  // OpenAI-compatible routes are listed from the provider's own /models.
  for (const id of ['opencode-go/listed-one', 'opencode-go/listed-two', 'local/listed-one']) assert.ok(ids.includes(id), id)
})

test('a wildcard route passes any model id to its provider with that route\'s own key', async () => {
  for (const [route, { key, env }] of Object.entries(ROUTES)) {
    const id = route === 'openrouter' ? 'vendor/brand-new-model:free' : 'brand-new-model-1'
    await chat(`${route}/${id}`)
    const call = stubs[env].seen.filter((seen) => seen.method === 'POST').at(-1)
    assert.equal(call.model, id, route)
    assert.equal(call.auth, `Bearer ${key}`, route)
    assert.equal(call.path, '/v1/chat/completions', route)
  }
})

test('a model outside every route is refused', async () => {
  const res = await fetch(`${base}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'x-litellm-api-key': `Bearer ${MASTER}`, 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'openai/gpt-4o', messages: [{ role: 'user', content: 'x' }] }),
  })
  assert.equal(res.status, 400)
})

test('metrics are served on their own port per key alias and model, with no message text', async () => {
  await chat('vibe/metrics-model')
  // The callbacks run after the response, so wait for the last series to appear.
  let text = ''
  for (const deadline = Date.now() + 30_000; Date.now() < deadline; await new Promise((resolve) => setTimeout(resolve, 1000))) {
    text = await (await fetch(`${metrics}/metrics/`)).text()
    if (/litellm_request_total_latency_metric_bucket\{[^}]*requested_model="vibe\/metrics-model"/.test(text) && /litellm_spend_metric_total/.test(text)) break
  }
  assert.match(text, /litellm_proxy_total_requests_metric_total\{[^}]*requested_model="vibe\/metrics-model"[^}]*status_code="200"/)
  assert.match(text, /litellm_total_tokens_metric_total\{[^}]*requested_model="vibe\/metrics-model"[^}]*\} 10\.0/)
  assert.match(text, /litellm_request_total_latency_metric_bucket\{[^}]*requested_model="vibe\/metrics-model"/)
  assert.match(text, /litellm_spend_metric_total/)
  assert.doesNotMatch(text, new RegExp(`${PROMPT}|${ANSWER}`))
  // Only the labels the config allows.
  assert.doesNotMatch(text, /user_email|client_ip|user_agent|end_user/)
})

test('no prompt or response text reaches the logs, the traces or the metrics', async () => {
  await chat('opencode-go/privacy-check')
  const deadline = Date.now() + 40_000
  while (Date.now() < deadline && !spans.some((body) => body.includes('chat_completion'))) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  // Traces were exported, so the absence below is not vacuous.
  assert.ok(spans.some((body) => body.includes('chat_completion')), 'no span was exported')
  assert.ok(spans.some((body) => body.includes('gen_ai.usage.total_tokens')), 'spans carry token counts')
  for (const body of spans) assert.doesNotMatch(body, new RegExp(`${PROMPT}|${ANSWER}`))
  // Python logging writes to stderr, so read both streams.
  const logs = spawnSync('docker', ['logs', NAME], { encoding: 'utf8' })
  assert.doesNotMatch(logs.stdout + logs.stderr, new RegExp(`${PROMPT}|${ANSWER}`))
  assert.doesNotMatch(await (await fetch(`${metrics}/metrics/`)).text(), new RegExp(`${PROMPT}|${ANSWER}`))
})
