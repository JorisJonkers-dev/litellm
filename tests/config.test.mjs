// The model list is the budget boundary: every route a virtual key can reach is named here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
// Comments are prose; only the configuration itself is checked.
const CONFIG = readFileSync(`${ROOT}/config/config.yaml`, 'utf8').replace(/^\s*#.*$/gm, '')
const ROUTES = [...CONFIG.matchAll(/^ {2}- model_name: (\S+)$/gm)].map((match) => match[1])

test('every route is a named cheap-agent model', () => {
  assert.deepEqual(ROUTES, [
    'vibe/zai-glm-5-3-flash',
    'vibe/deepseek-v4-1-flash-1017',
    'vibe-study/zai-glm-5-3-flash',
    'vibe-study/deepseek-v4-1-flash-1017',
    'opencode-go/glm-5.3-flash',
    'opencode-go/qwen3.8-flash',
    'opencode-go/mimo-v2.6-flash',
  ])
})

test('no wildcard, OpenRouter or Zen route', () => {
  assert.doesNotMatch(CONFIG, /model_name: \S*\*/)
  assert.doesNotMatch(CONFIG, /openrouter/i)
  assert.doesNotMatch(CONFIG, /opencode\.ai\/zen\/v1/)
})

test('upstream keys are environment names, never values', () => {
  const keys = [...CONFIG.matchAll(/api_key: (\S+?)\s*[},]/g)].map((match) => match[1])
  assert.equal(keys.length, ROUTES.length)
  for (const key of keys) assert.match(key, /^os\.environ\/[A-Z_]+$/)
  assert.match(CONFIG, /master_key: os\.environ\/LITELLM_MASTER_KEY/)
})
