// The paid-leak alert fires on a synthetic series: promtool's own rule tests, run from the
// prom/prometheus image (promtool is on PATH when installed, which wins).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const DIR = fileURLToPath(new URL('../deploy/alerts', import.meta.url))
const PROMTOOL_IMAGE = 'prom/prometheus:v3.5.0'

function promtool(...args) {
  const local = spawnSync('promtool', args, { cwd: DIR, encoding: 'utf8' })
  if (!local.error) return local
  return spawnSync('docker', ['run', '--rm', '-v', `${DIR}:/r:ro`, '-w', '/r', '--entrypoint', 'promtool', PROMTOOL_IMAGE, ...args], { encoding: 'utf8' })
}

test('the rules are valid', () => {
  const run = promtool('check', 'rules', 'litellm-tier.rules.yaml')
  assert.equal(run.status, 0, run.stdout + run.stderr)
})

test('a free request served by a priced deployment fires LiteLLMFreeTierPaidLeak, and nothing else does', () => {
  const run = promtool('test', 'rules', 'litellm-tier.rules.test.yaml')
  assert.equal(run.status, 0, run.stdout + run.stderr)
})
