/** Real DSH tools + PTC provider + durable Session; no model. */
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import * as Plugin from '../lib/index.js'

if (!process.env.DSH_CHECKOUT) throw new Error('Set DSH_CHECKOUT to built upstream checkout')
const load = path => import(pathToFileURL(join(process.env.DSH_CHECKOUT, path)).href)
const { Context } = await load('vendor/cordis/lib/index.js')
const { mountAgentLoopTestDependencies, mountAgentLoopTestHarness } = await load('packages/test-support/agent-loop-testkit/lib/index.js')
const directory = await mkdtemp(join(tmpdir(), 'dsh-label-ptc-'))
const root = join(directory, 'store')
const config = Plugin.Config({ root, pythonExecutable: process.env.LABEL_PYTHON ?? resolve('.venv/Scripts/python.exe') })
const ctx = new Context()
let nested = 0
try {
  for (const name of ['subprocess', 'sandbox']) await ctx.plugin((await load(`packages/${name}/${name}-local/lib/index.js`)).default, {})
  const policy = ctx.plugin((await load('packages/sandbox/sandbox-policy/lib/index.js')).default, { mode: 'workspace-write', workspaceRoot: directory })
  const runtime = ctx.plugin((await load('packages/ptc-runtime/ptc-runtime-node/lib/index.js')).default, { timeoutMs: 120000, maxTimeoutMs: 120000 })
  await mountAgentLoopTestDependencies(ctx, { workingDirectory: true, tools: { mode: 'ptc' } })
  await policy; await runtime
  const harness = await mountAgentLoopTestHarness(ctx)
  let plugin = ctx.plugin(Plugin, config); await plugin
  assert.equal(ctx.tools.schemas().filter(t => t.name.startsWith('label_')).length, 17)
  ctx.tools.guard(exec => { if (exec.name.startsWith('label_')) nested++; return undefined })
  const agent = await harness.create('label-workbench-check', {}, { cwd: directory })
  const call = async (code, id) => {
    const batch = await ctx.tools.execute({ agent, calls: [{ type: 'tool-call', id, name: 'run_code', arguments: JSON.stringify({ code, description: 'Validate annotation rule revisions', timeoutMs: 120000 }) }], signal: AbortSignal.timeout(150000) })
    const result = batch.results[0].result
    assert.equal(result.isError, false, JSON.stringify(result))
    return result.value.result
  }
  const rule1 = { mode: 'decision_list', labels: ['a', 'b'], guidelines: 'Complete ordered literal policy', rules: [{ contains_any: ['return'], label: 'a' }], default: 'b' }
  const rule2 = { ...rule1, rules: [{ contains_any: ['refund'], label: 'a' }] }
  const first = await call(`
    const p=await tools.label_project_create({name:'PTC annotation',records:[{id:'1',text:'return'},{id:'2',text:'hello'}],rule:${JSON.stringify(rule1)},request_key:'create'});
    const old=await tools.label_job_create({project_id:p.project_id,expected_version:1,request_key:'old-job'});
    const lease=await tools.label_job_claim({job_id:old.job_id});
    const j=await tools.label_job_create({project_id:p.project_id,expected_version:1,request_key:'current-job'});
    await tools.label_job_step({job_id:j.job_id,request_key:'initial-step'});
    const preview=await tools.label_rule_preview({project_id:p.project_id,rule:${JSON.stringify(rule2)}});
    await tools.label_rule_activate({project_id:p.project_id,expected_version:1,rule:${JSON.stringify(rule2)},request_key:'change'});
    const stale=await tools.label_job_submit({job_id:old.job_id,results:lease.items.map(x=>({record_id:x.record_id,token:x.token,label:'a'}))});
    const next=await tools.label_job_create({project_id:p.project_id,expected_version:2,request_key:'next-job'});
    await tools.label_job_step({job_id:next.job_id});
    const delivery=await tools.label_dataset_export({project_id:p.project_id,expected_version:2});
    return {project_id:p.project_id,preview,stale,delivery};`, 'workflow')
  const workflowCalls=nested
  assert.equal(first.preview.carry_count, 1)
  assert.ok(first.stale.outcomes.every(x => x.reason === 'stale_rule'))
  await plugin.dispose()
  assert.equal(ctx.tools.schemas().filter(t => t.name.startsWith('label_')).length, 0)
  plugin=ctx.plugin(Plugin, config); await plugin
  const resumed=await call(`return await tools.label_workbench_resume({});`, 'resume')
  assert.equal(resumed.projects[0].id, first.project_id)
  const replay=await call(`return await tools.label_project_create({name:'PTC annotation',records:[{id:'1',text:'return'},{id:'2',text:'hello'}],rule:${JSON.stringify(rule1)},request_key:'create'});`, 'replay')
  assert.equal(replay.project_id, first.project_id)
  await plugin.dispose()
  const report={kind:'real-dsh-ptc-no-model',model_calls:0,tool_count:17,outer_calls_for_workflow:1,nested_calls_for_workflow:workflowCalls,stale_callback_isolated:true,safe_carry_count:1,reload_same_session_recovered:true,idempotent_create:true,unload_removed_tools:true,limitation:'Plugin remount is not a full DSH process restart. Managed local store uses host RPC, not DSH subprocess confinement.'}
  await mkdir('docs',{recursive:true})
  await writeFile('docs/dsh-ptc-results.json',JSON.stringify(report,null,2)+'\n')
  console.log(JSON.stringify(report))
} finally { await ctx.fiber.dispose() }
