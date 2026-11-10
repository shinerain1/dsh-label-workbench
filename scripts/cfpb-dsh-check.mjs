/** Actual DSH formal tools + PTC on official complaints; no LLM or human. */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import * as Plugin from '../lib/index.js'

if (!process.env.DSH_CHECKOUT) throw new Error('Set DSH_CHECKOUT to built upstream checkout')
const inputFile = resolve('.verification/cfpb/ptc-input.json')
const inputBytes = await readFile(inputFile)
const input = JSON.parse(inputBytes)
const load = path => import(pathToFileURL(join(process.env.DSH_CHECKOUT, path)).href)
const { Context } = await load('vendor/cordis/lib/index.js')
const { mountAgentLoopTestDependencies, mountAgentLoopTestHarness } = await load('packages/test-support/agent-loop-testkit/lib/index.js')
const directory = await mkdtemp(join(tmpdir(), 'dsh-cfpb-ptc-'))
const python = process.env.LABEL_PYTHON ?? resolve('.venv/Scripts/python.exe')
const config = Plugin.Config({ root: join(directory, 'store'), pythonExecutable: python })
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
  ctx.tools.guard(exec => { if (exec.name.startsWith('label_')) nested++; return undefined })
  const agent = await harness.create('cfpb-real-data-check', {}, { cwd: directory })
  const call = async (code, id) => {
    const batch = await ctx.tools.execute({ agent, calls: [{ type: 'tool-call', id, name: 'run_code', arguments: JSON.stringify({ code, description: 'Replay official CFPB taxonomy migration', timeoutMs: 120000 }) }], signal: AbortSignal.timeout(150000) })
    const result = batch.results[0].result
    assert.equal(result.isError, false, JSON.stringify(result))
    return result.value.result
  }
  const first = await call(`
    const input=${JSON.stringify(input)};
    const p=await tools.label_project_create({name:'CFPB real complaints',records:input.records,rule:input.rule1,request_key:'cfpb-create'});
    const old=await tools.label_job_create({project_id:p.project_id,expected_version:1,record_ids:input.records.slice(0,10).map(x=>x.id)});
    const late=await tools.label_job_claim({job_id:old.job_id});
    const j=await tools.label_job_create({project_id:p.project_id,expected_version:1});
    const v1=await tools.label_job_claim({job_id:j.job_id,limit:100});
    await tools.label_job_submit({job_id:j.job_id,results:v1.items.map(x=>({record_id:x.record_id,token:x.token,label:input.old[x.record_id]}))});
    const preview=await tools.label_rule_preview({project_id:p.project_id,rule:input.rule2});
    await tools.label_rule_activate({project_id:p.project_id,expected_version:1,rule:input.rule2,request_key:'cfpb-change'});
    const next=await tools.label_job_create({project_id:p.project_id,expected_version:2});
    const v2=await tools.label_job_claim({job_id:next.job_id,limit:100});
    await tools.label_job_submit({job_id:next.job_id,results:v2.items.map(x=>({record_id:x.record_id,token:x.token,label:input.new[x.record_id]}))});
    const stale=await tools.label_job_submit({job_id:old.job_id,results:late.items.map(x=>({record_id:x.record_id,token:x.token,label:input.old[x.record_id]}))});
    const delivery=await tools.label_dataset_export({project_id:p.project_id,expected_version:2});
    return {project_id:p.project_id,review_count:preview.review_count,carry_count:preview.carry_count,isolated:stale.outcomes.filter(x=>x.reason==='stale_rule').length,delivery};`, 'workflow')
  const workflowCalls = nested
  assert.equal(first.review_count, input.records.length)
  assert.equal(first.carry_count, 0)
  assert.equal(first.isolated, 10)
  await plugin.dispose()
  plugin = ctx.plugin(Plugin, config); await plugin
  const resumed = await call('return await tools.label_workbench_resume({});', 'resume')
  assert.ok(resumed.projects.some(p => p.id === first.project_id))
  const audit = JSON.parse(execFileSync(python, ['-c', `
import json,sys,zipfile
from dsh_label_workbench.service import verify_export
source=json.load(open(sys.argv[2],encoding='utf-8'))
audit=verify_export(sys.argv[1])
with zipfile.ZipFile(sys.argv[1]) as z:
    rows=[json.loads(line) for line in z.read('data.jsonl').decode('utf-8').splitlines()]
texts={r['id']:r['text'] for r in source['records']}
assert {r['id'] for r in rows}==set(source['new'])
assert all(r['label']==source['new'][r['id']] and r['rule_version']==2 and r['text']==texts[r['id']] for r in rows)
print(json.dumps(audit))
`, first.delivery.path, inputFile], { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } }))
  const report = {
    kind: 'real-dsh-ptc-real-cfpb-data-no-model', model_calls: 0,
    dsh_commit: execFileSync('git', ['-c', `safe.directory=${process.env.DSH_CHECKOUT.replaceAll('\\', '/')}`, 'rev-parse', 'HEAD'], { cwd: process.env.DSH_CHECKOUT, encoding: 'utf8' }).trim(),
    input_sha256: createHash('sha256').update(inputBytes).digest('hex'),
    script_sha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),
    records: input.records.length, outer_workflow_calls: 1, nested_workflow_calls: workflowCalls,
    selected_for_review: first.review_count, safely_carried: first.carry_count,
    late_callbacks_isolated: first.isolated, session_recovered_after_plugin_remount: true,
    exported_texts_and_reference_labels_match: true, export_audit: audit,
    limitations: ['Reference labels supplied from official taxonomy; no LLM labeling accuracy measurement', 'Plugin remount in one DSH process; not a full process restart', 'Managed host RPC store is not DSH subprocess confinement']
  }
  await writeFile('docs/cfpb/dsh-ptc-results.json', JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report))
  await writeFile('.verification/cfpb/dsh-location.json', JSON.stringify({ directory, export: first.delivery.path }))
} finally { await ctx.fiber.dispose() }
