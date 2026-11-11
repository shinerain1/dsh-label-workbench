/** Validate a downloaded installation using real DSH profile resolution and tools. */
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const checkout=process.env.DSH_CHECKOUT
const profileDir=process.env.LABEL_PROFILE
const python=process.env.LABEL_PYTHON
if (!checkout || !profileDir || !python) throw new Error('Set DSH_CHECKOUT, LABEL_PROFILE, LABEL_PYTHON')
const load=path=>import(pathToFileURL(join(checkout,path)).href)
const {Context}=await load('vendor/cordis/lib/index.js')
const {createRuntimeResolution,loadProfileDirectory,PluginPackages}=await load('packages/boot/app-boot/lib/index.js')
const anchor=join(checkout,'apps/cli/package.json')
const profile=loadProfileDirectory('label-public-check',profileDir,anchor)
assert.deepEqual(profile.skippedBundles,[])
const resolution=await createRuntimeResolution({installAnchor:anchor,profile,home:resolve(profileDir,'../..')})
const ctx=new Context()
let calls=0
try {
  await ctx.plugin(PluginPackages,{resolution})
  const require=createRequire(pathToFileURL(join(profileDir,'package.json')))
  const pluginPath=require.resolve('dsh-label-workbench')
  assert.ok(pluginPath.toLowerCase().includes(resolve(profileDir).toLowerCase()))
  const peerRequire=createRequire(pluginPath)
  for(const peer of ['@deepseek-ai/cordis','@deepseek-ai/dsh-tools']) {
    assert.ok(peerRequire.resolve(peer).toLowerCase().startsWith(resolve(checkout).toLowerCase()),peer)
  }
  const Plugin=await import(pathToFileURL(pluginPath).href)
  await ctx.plugin((await load('packages/core/system-prompt/lib/index.js')).default)
  await ctx.plugin((await load('packages/core/tools/lib/index.js')).default)
  await ctx.plugin(await load('packages/core/tool-executor-native/lib/index.js'))
  const plugin=ctx.plugin(Plugin,Plugin.Config({root:resolve(profileDir,'../../validation-store'),pythonExecutable:python}))
  await plugin
  assert.equal(ctx.tools.schemas().filter(x=>x.name.startsWith('label_')).length,17)
  const call=async(name,args={})=>{
    const batch=await ctx.tools.execute({calls:[{type:'tool-call',id:`release-${++calls}`,name,arguments:JSON.stringify(args)}],signal:AbortSignal.timeout(120000)})
    const result=batch.results[0].result
    assert.equal(result.isError,false,JSON.stringify(result))
    return result.value
  }
  const health=await call('label_health')
  assert.equal(health.version,'0.1.0')
  const rule={mode:'decision_list',labels:['a','b'],guidelines:'Complete literal rules',rules:[{contains_any:['old'],label:'a'}],default:'b'}
  const p=await call('label_project_create',{name:'Public package smoke',records:[{id:'1',text:'old'},{id:'2',text:'hello'}],rule,request_key:'release-create'})
  const old=await call('label_job_create',{project_id:p.project_id,expected_version:1})
  const lease=await call('label_job_claim',{job_id:old.job_id})
  const initial=await call('label_job_create',{project_id:p.project_id,expected_version:1})
  await call('label_job_step',{job_id:initial.job_id})
  await call('label_rule_activate',{project_id:p.project_id,expected_version:1,rule:{...rule,rules:[]}})
  const current=await call('label_job_create',{project_id:p.project_id,expected_version:2})
  await call('label_job_step',{job_id:current.job_id})
  const late=await call('label_job_submit',{job_id:old.job_id,results:lease.items.map(x=>({record_id:x.record_id,token:x.token,label:x.record_id==='1'?'a':'b'}))})
  assert.ok(late.outcomes.every(x=>x.reason==='stale_rule'))
  const delivery=await call('label_dataset_export',{project_id:p.project_id,expected_version:2})
  const audit=JSON.parse(execFileSync(python,['-m','dsh_label_workbench','verify',delivery.path],{encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}}))
  assert.equal(audit.valid,true)
  await plugin.dispose()
  assert.equal(ctx.tools.schemas().filter(x=>x.name.startsWith('label_')).length,0)
  const result={public_profile_resolution:'passed',sdk_peers_from_dsh_host:true,native_tools:17,tool_calls:calls,backend_version:health.version,rule_migration:'passed',late_callbacks_isolated:late.outcomes.length,export_verified:audit.valid,unload:'passed',model_calls:0}
  if(process.env.LABEL_VALIDATION_OUTPUT) await writeFile(process.env.LABEL_VALIDATION_OUTPUT,JSON.stringify(result,null,2)+'\n')
  console.log(JSON.stringify(result))
}finally{await ctx.fiber.dispose()}
