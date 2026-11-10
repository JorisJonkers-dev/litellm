// The published image is LiteLLM with the estate's model list built in. These tests build the
// image, start it the way deploy/litellm.project.yml runs it (read-only root, only its writable
// paths writable, no database) and talk to it over HTTP. IMAGE skips the build and tests an
// image that already exists.
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const IMAGE = process.env.IMAGE || 'litellm-image-test:local'
const NAME = `litellm-image-test-${process.pid}`
const PROJECT = readFileSync(`${ROOT}/deploy/litellm.project.yml`, 'utf8')
const WRITABLE = PROJECT.match(/writablePaths: \[([^\]]*)\]/)[1].split(',').map((path) => path.trim())
const MASTER = 'sk-image-test-master'
let base

function docker(...args) {
  return execFileSync('docker', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
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

before(async () => {
  if (!process.env.IMAGE) docker('build', '-t', IMAGE, '.')
  docker(
    'run', '-d', '--name', NAME, '-p', '127.0.0.1::4000', '--read-only',
    ...WRITABLE.flatMap((path) => ['--tmpfs', path]),
    '-e', `LITELLM_MASTER_KEY=${MASTER}`,
    '-e', 'LITELLM_SALT_KEY=sk-image-test-salt',
    '-e', 'VIBE_API_KEY=unused', '-e', 'VIBE_STUDY_API_KEY=unused', '-e', 'OPENCODE_GO_API_KEY=unused',
    '-e', 'LITELLM_TELEMETRY=False', '-e', 'LITELLM_MODE=PRODUCTION', '-e', 'DISABLE_ADMIN_UI=True',
    IMAGE,
  )
  base = `http://127.0.0.1:${docker('port', NAME, '4000/tcp').split('\n')[0].split(':').pop()}`
  await waitForLive(base, 240_000)
})

after(() => {
  try {
    docker('rm', '-f', NAME)
  } catch {
    // already gone
  }
})

test('the proxy lists exactly the configured models, and only for the master key', async () => {
  const configured = [...readFileSync(`${ROOT}/config/config.yaml`, 'utf8').matchAll(/^ {2}- model_name: (\S+)$/gm)]
    .map((match) => match[1])
  const denied = await fetch(`${base}/v1/models`)
  assert.equal(denied.status, 401)
  const res = await fetch(`${base}/v1/models`, { headers: { 'x-litellm-api-key': `Bearer ${MASTER}` } })
  assert.equal(res.status, 200)
  const served = (await res.json()).data.map((model) => model.id).sort()
  assert.deepEqual(served, [...configured].sort())
})
