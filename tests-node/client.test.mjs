import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { BackendClient } from '../lib/client.js'

test('real Python RPC: typed mutations, retry identity and human boundary', async () => {
  const root = await mkdtemp(join(tmpdir(), 'label-rpc-'))
  const client = new BackendClient({ pythonExecutable: process.env.LABEL_PYTHON ?? resolve('.venv/Scripts/python.exe'), root, requestTimeoutMs: 10000 })
  const signal = AbortSignal.timeout(30000)
  try {
    assert.equal((await client.call('health', {}, signal)).backend_version, '0.1.0')
    const args = { name: 'rpc', records: [{ id: '1', text: 'a' }], rule: { mode: 'guidelines', guidelines: 'test', labels: ['a', 'b'] } }
    const identity = { session_id: 'rpc-session', call_id: 'create' }
    const first = await client.call('create', args, signal, identity)
    const replay = await client.call('create', args, signal, identity)
    assert.equal(first.project_id, replay.project_id)
    await assert.rejects(client.call('review', { project_id: first.project_id }, signal), /HUMAN_CHANNEL_REQUIRED/)
    await assert.rejects(client.call('create', { ...args, name: 'conflict' }, signal, identity), /KEY_CONFLICT/)
  } finally { await client.dispose(); await rm(root, { recursive: true, force: true }) }
})

test('cancelled invocation does not spawn a backend', async () => {
  const client = new BackendClient({ pythonExecutable: 'nonexistent', root: resolve('.verification/unused'), requestTimeoutMs: 1000 })
  await assert.rejects(client.call('health', {}, AbortSignal.abort()), /abort/i)
  await client.dispose()
})
