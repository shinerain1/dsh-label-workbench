"""Prepare/run/audit a small real DSH two-arm pilot. Raw sessions stay local."""
import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from benchmark import RULE1,RULE2,dataset
from dsh_label_workbench.service import Workbench

ROOT=Path(__file__).resolve().parents[1]
ARMS=("plain-script","with-plugin")

def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def read(path): return json.loads(path.read_text(encoding="utf-8-sig"))

def prepare():
    root=Path(tempfile.mkdtemp(prefix="dsh-label-agent-"))
    rows=dataset(24,7)
    human={r["id"]:"其他" for r in rows[:2]}
    for arm in ARMS:
        folder=root/arm; folder.mkdir()
        save(folder/"rules-v1.json",RULE1); save(folder/"rules-v2.json",RULE2)
        save(folder/"records.json",[{k:r[k] for k in ("id","text")} for r in rows])
        original=[dict(id=r["id"],text=r["text"],label=human.get(r["id"],r["old"]),rule_version=1,needs_review=False,source="human" if r["id"] in human else "machine",history=[dict(rule_version=1,label=human[r["id"]],reviewer="fixture-reviewer")] if r["id"] in human else []) for r in rows]
        save(folder/"state-v1.json",original)
        overlay=[{"id":"credentials","disabled":False,"config":{"path":str(Path.home()/".dsh/.credentials.yaml"),"watch":False}},{"id":"agent-default-model","config":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"low"}},{"id":"llm-deepseek","config":{"maxTokens":8192,"retryPolicy":{"mode":"normal","maxRetries":0},"streamIdleTimeoutMs":60000}}]
        if arm=="with-plugin":
            w=Workbench(folder/"store")
            pid=w.call("create",dict(name="真实 DSH 规则变更试验",records=[{k:r[k] for k in ("id","text")} for r in rows],rule=RULE1))["project_id"]
            old=w.call("job_create",dict(project_id=pid,expected_version=1))["job_id"]
            lease=w.call("claim",dict(job_id=old,lease_seconds=3600))
            job=w.call("job_create",dict(project_id=pid,expected_version=1))["job_id"]
            w.call("step",dict(job_id=job))
            for rid,label in human.items():
                with w.connect() as con: cur=w.current(con,pid,rid)
                w.call("review",dict(project_id=pid,record_id=rid,expected_version=1,expected_revision=cur["revision"],label=label,reviewer="fixture-reviewer",note="Pre-existing simulated human review, not an actual participant"),human=True)
            truth={r["id"]:r["old"] for r in rows}
            save(folder/"late-results.json",dict(job_id=old,results=[dict(record_id=r["record_id"],token=r["token"],label=truth[r["record_id"]]) for r in lease["items"]]))
            save(folder/"handoff.json",dict(project_id=pid))
            overlay.append({"insert":[{"id":"label-workbench","name":str(ROOT/"lib/index.js"),"config":{"pythonExecutable":sys.executable,"root":str(folder/"store")}}]})
            specific="使用已经安装的 label_* 工具处理 handoff.json 中的项目。不要直接编辑后台 SQLite，不要重新创建项目。可以用 label_job_step 执行完整的关键词标准，用 PTC 循环批量调用。"
        else:
            save(folder/"late-results.json",dict(rule_version=1,results=[dict(id=r["id"],label=r["old"]) for r in rows]))
            specific="用普通 Python/JSON 标注脚本完成，不使用标注工作台库或 label_* 工具。可以写完善的版本保护和恢复逻辑；不是要求你使用一个故意不可靠的脚本。保存可再次运行的 workflow.py。"
        save(folder/"overlay.yml",overlay)  # JSON is valid YAML.
        prompt=f"""请完成一个本地标注规则变更任务。工作目录是 {folder}。{specific}
records.json 是不可变的 24 条文本，rules-v1.json/rules-v2.json 是完整可执行顺序规则，首条 contains_any 忽略大小写命中后取 label，否则 default。state-v1.json 是已有标签和两条人工审核记录。
现在标准从 v1 改为 v2。分析影响并完成机器可以完成的重标注；两条历史人工记录必须保留，标记为等待人工按 v2 重新确认，你不能替人审批。
规则切换并重标注后，实际处理 late-results.json（迟到的 v1 后台回调）。不允许它污染 v2 或覆盖人工记录。正确隔离它也是处理。相同回调再处理一次应安全。
写出统一交接文件 state-v2.json，是含 24 个对象的数组，每个含 id,text,label,rule_version,needs_review,source,history（人工审核历史数组）。机器完成的记录必须对应 v2；未确认的人工记录保留旧标签与版本并 needs_review=true。再写 result.json，含 active_version,pending_human_ids,export_allowed,isolated_callback_count,以及你生成的脚本/产物路径。只有全复核才允许最终数据集导出；当前不要伪造完整数据集。
你可以读取这些文件和当前目录自己的结果，执行本机 Python/代码工具。禁止读本项目其他实验、源码、oracle、其他 arm；不联网、不安装、不调用其他模型或子 Agent、不修改凭据。工作目录外不写文件。不要询问我；遇到不能确认的人审数据如实保留待审核。尽量用少量工具调用，但准确性优先。最后简短报告结果。"""
        (folder/"task.txt").write_text(prompt,encoding="utf-8")
        (folder/"followup.txt").write_text("这是同一项目的新进程会话。请从持久状态恢复，复查 result.json 和 state-v2.json，并再次处理同一 late-results.json，确认旧任务仍不能改变结果。人工还未确认，不能导出最终数据集。不得替人审核。写 resume.json，含 recovered、pending_human_ids、export_allowed。不要重建项目或重新标注已完成记录。仍不得读其他实验/源码，不联网、不安装、不用子 Agent。",encoding="utf-8")
    (ROOT/".verification").mkdir(exist_ok=True)
    save(ROOT/".verification/agent-pilot-location.json",{"root":str(root),"human":human})
    print(json.dumps({"prepared":str(root),"records":24,"human_reviews":2}))

def run(arm,turn):
    root=Path(read(ROOT/".verification/agent-pilot-location.json")["root"])
    folder=root/arm; checkout=Path("E:/dsharness")
    env={**os.environ,"DSH_HOME":str(folder/"dsh-home"),"TSX_TSCONFIG_PATH":str(checkout/"tsconfig.json"),"NODE_USE_ENV_PROXY":"1","PYTHONUTF8":"1"}
    cmd=["node","--import",(checkout/"node_modules/tsx/dist/esm/index.mjs").as_uri(),str(checkout/"apps/cli/src/bin.ts"),"headless","--patch",str(folder/"overlay.yml"),"--approval-policy","never","--json"]
    if turn==2:
        events=[json.loads(s) for s in (folder/"turn-1.jsonl").read_text(encoding="utf-8").splitlines() if s.startswith("{")]
        cmd.extend(["--session-id",next(e["sessionId"] for e in events if e["type"]=="session")])
    cmd.append("-")
    prompt=(folder/("task.txt" if turn==1 else "followup.txt")).read_text(encoding="utf-8")
    started=time.monotonic()
    with (folder/f"turn-{turn}.jsonl").open("w",encoding="utf-8") as out,(folder/f"turn-{turn}.stderr").open("w",encoding="utf-8") as err:
        proc=subprocess.Popen(cmd,cwd=folder,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,text=True,encoding="utf-8")
        try: proc.communicate(prompt,timeout=600); code=proc.returncode
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],capture_output=True)
            proc.wait(timeout=20); code="deadline"
    summary={"arm":arm,"turn":turn,"exit_code":code,"seconds":round(time.monotonic()-started,3)}
    save(folder/f"timing-{turn}.json",summary)
    print(json.dumps(summary))

def audit():
    location=read(ROOT/".verification/agent-pilot-location.json"); root=Path(location["root"])
    truth=dataset(24,7); human=location["human"]
    report={"kind":"live-dsh-agent-pilot","model":"deepseek-official/deepseek-flash","reasoning_effort":"low","replicates_per_arm":1,"turns_per_arm":2,"records":24,"human_reviews":"2 pre-seeded simulated reviews; no real human participation","budget":"600 seconds per turn, maxTokens 8192 per completion; no equal-token constraint","arms":{}}
    for arm in ARMS:
        folder=root/arm; usage=collections.Counter(); calls=collections.Counter(); errors=[]; sessions=[]; completed=[]; final_reasons=[]; completions=0
        for turn in (1,2):
            path=folder/f"turn-{turn}.jsonl"
            events=[json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.startswith("{")] if path.exists() else []
            sessions.extend(e["sessionId"] for e in events if e["type"]=="session")
            completed.append(any(e["type"]=="final" and e.get("reason",{}).get("kind")=="completed" for e in events))
            final_reasons.append([e.get("reason",{}).get("kind") for e in events if e["type"]=="final"])
            for e in events:
                if e["type"]=="tool_call": calls[e["tool"]]+=1
                if e["type"]=="tool_result" and e.get("status")=="error": errors.append(str(e.get("result",""))[:180])
                if e["type"]=="status" and e.get("phase")=="step_end": usage.update(e.get("usage",{})); completions+=1
        failures=[]
        state=read(folder/"state-v2.json") if (folder/"state-v2.json").exists() else []
        if not isinstance(state,list): failures.append("state-v2.json is not an array"); state=[]
        mapped={r.get("id"):r for r in state}
        for row in truth:
            x=mapped.get(row["id"])
            if x is None: failures.append(row["id"]+": missing"); continue
            if x.get("text")!=row["text"]: failures.append(row["id"]+": content changed")
            if row["id"] in human:
                if x.get("label")!=human[row["id"]] or x.get("rule_version")!=1 or x.get("needs_review") not in (True,1) or x.get("source")!="human" or not x.get("history"): failures.append(row["id"]+": human protection failed")
            elif x.get("label")!=row["new"] or x.get("rule_version")!=2 or x.get("needs_review") not in (False,0): failures.append(row["id"]+": wrong/unresolved machine decision")
        result=read(folder/"result.json") if (folder/"result.json").exists() else {}
        resume=read(folder/"resume.json") if (folder/"resume.json").exists() else {}
        if result.get("export_allowed") is not False or set(result.get("pending_human_ids",[]))!=set(human): failures.append("incorrect export/review gate")
        if resume.get("export_allowed") is not False or set(resume.get("pending_human_ids",[]))!=set(human): failures.append("incorrect recovery report")
        backend_checked=False
        if arm=="with-plugin":
            w=Workbench(folder/"store"); pid=read(folder/"handoff.json")["project_id"]
            status=w.call("status",dict(project_id=pid)); backend_checked=status["rule_version"]==2 and status["pending"]==2
            if not backend_checked: failures.append("backend state disagrees")
            for x in w.call("queue",dict(project_id=pid,pending_only=False))["items"]:
                target=next(r for r in truth if r["id"]==x["id"])
                if x["id"] in human:
                    if x["decision"]["origin"]!="human" or x["decision"]["label"]!=human[x["id"]] or not x["needs_review"]: failures.append("backend human failed")
                elif x["decision"]["version"]!=2 or x["decision"]["label"]!=target["new"]: failures.append("backend label failed")
        report["arms"][arm]={"completed":completed,"final_reasons":final_reasons,"model_completions":completions,"same_session_new_process":len(sessions)==2 and sessions[0]==sessions[1],"records_delivered":len(state),"audit_failures":failures,"tool_calls":dict(calls),"tool_error_count":len(errors),"usage":dict(usage),"timings":[read(folder/f"timing-{t}.json") for t in (1,2) if (folder/f"timing-{t}.json").exists()],"backend_independently_checked":backend_checked,"export_blocked":result.get("export_allowed") is False}
    save(ROOT/"docs/agent-pilot-results.json",report)
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("action",choices=["prepare","run","audit"]); p.add_argument("--arm",choices=ARMS); p.add_argument("--turn",type=int,choices=[1,2],default=1); a=p.parse_args()
    if a.action=="prepare": prepare()
    elif a.action=="run": run(a.arm,a.turn)
    else: audit()
