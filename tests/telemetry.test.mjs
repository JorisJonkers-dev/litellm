// The tier telemetry callback (config/tier_telemetry.py) needs LiteLLM, which only the image has, so
// its checks (tests/check_tier_telemetry.py) run inside the image against LiteLLM's mock testing.
// IMAGE skips the build and tests an image that already exists.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const IMAGE = process.env.IMAGE || 'litellm-image-test:local'

test('a forced failover shows the requested tier and the serving deployment differing, and a paid serve is marked priced', () => {
  if (!process.env.IMAGE) execFileSync('docker', ['build', '-t', IMAGE, '.'], { cwd: ROOT, stdio: 'ignore' })
  const run = spawnSync('docker', [
    'run', '--rm', '-v', `${ROOT}/tests:/t:ro`, '--entrypoint', '/app/.venv/bin/python', IMAGE, '/t/check_tier_telemetry.py',
  ], { encoding: 'utf8' })
  assert.equal(run.status, 0, run.stderr.slice(-4000))
})
