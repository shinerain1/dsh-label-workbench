"""Paired deterministic rule-change experiment. Humans are scripted events, not participants."""
import copy
import hashlib
import json
import random
import tempfile
import time
from pathlib import Path
from dsh_label_workbench.service import Workbench, evaluate, verify_export

ROOT=Path(__file__).resolve().parents[1]
RULE1={"mode":"decision_list","labels":["退款","售后","物流","其他"],"guidelines":"以下有序关键词列表就是完整标准；忽略大小写，首条命中即采用，否则用默认标签。", "rules":[{"contains_any":["退款","退货"],"label":"退款"},{"contains_any":["损坏","坏了"],"label":"售后"},{"contains_any":["物流","快递"],"label":"物流"}],"default":"其他"}
RULE2={**RULE1,"rules":[{"contains_any":["退款","退钱"],"label":"退款"},{"contains_any":["退货","损坏","坏了","破损"],"label":"售后"},{"contains_any":["物流","快递"],"label":"物流"}]}
# Independently assigned outcomes for these exact templates, including overlap/precedence.
TEMPLATES=[("我想申请退款","退款","退款"),("申请退货，尺寸不合适","退款","售后"),("商品破损了","其他","售后"),("请帮我退钱","其他","退款"),("快递一直没到","物流","物流"),("物流很快，谢谢","物流","物流"),("东西坏了怎么办","售后","售后"),("这个颜色还有货吗","其他","其他"),("已经退款，为什么还要退货","退款","退款"),("收到破损快递","物流","售后"),("商品损坏，申请退货","退款","售后"),("你好，想咨询一下","其他","其他")]

def dataset(n,seed):
    rows=[]
    for i in range(n):
        text,old,new=TEMPLATES[i%len(TEMPLATES)]
        rows.append(dict(id=f"r{i:05}",text=f"{text}（工单{i}）",old=old,new=new))
    random.Random(seed).shuffle(rows)
    return rows

def baseline(rows,human,late,guarded):
    start=time.perf_counter()
    current={r["id"]:dict(label=r["old"],version=1,human=False) for r in rows}
    human_history=[]
    for rid,label in human.items():
        current[rid]=dict(label=label,version=1,human=True)
        human_history.append((rid,1,label))
    old_words={t for x in RULE1["rules"] for t in x["contains_any"]}
    new_words={t for x in RULE2["rules"] for t in x["contains_any"]}-old_words
    if guarded: selected={r["id"] for r in rows}
    else: selected={r["id"] for r in rows if any(w in r["text"] for w in new_words)}
    machine=0
    for row in rows:
        rid=row["id"]
        if rid in selected and not (guarded and rid in human):
            current[rid]=dict(label=row["new"],version=2,human=False); machine+=1
        elif guarded and rid not in human:
            raise AssertionError("guarded baseline should process all nonhuman records")
        elif not guarded:
            current[rid]["version"]=2  # naive manifest-only relabeling
    for rid,label in human.items():
        current[rid]=dict(label=label,version=2,human=True)
        human_history.append((rid,2,label))
    isolated=0
    for row in late:
        if guarded: isolated+=1
        else: current[row["id"]]=dict(label=row["old"],version=1,human=False)
    changed={r["id"] for r in rows if r["old"]!=r["new"]}
    expected={r["id"]:human.get(r["id"],r["new"]) for r in rows}
    return dict(arm="guarded_full_reprocess_script" if guarded else "simple_json_script",missed_review=len(changed-selected-set(human)),stale_current=sum(v["version"]!=2 for v in current.values()),human_overwritten=sum(current[rid]["label"]!=label or not current[rid]["human"] for rid,label in human.items()),wrong_final_labels=sum(current[rid]["label"]!=label for rid,label in expected.items()),new_machine_evaluations=machine,human_confirmations=len(human),isolated_callbacks=isolated,seconds=round(time.perf_counter()-start,6),retained_human_history=len(human_history),scope_selected=len(selected),artifact_verified=False)

def plugin(rows,human,late,store):
    start=time.perf_counter(); w=Workbench(store)
    pid=w.call("create",dict(name="客服规则变更实验",records=[{k:r[k] for k in ("id","text")} for r in rows],rule=RULE1),"create")["project_id"]
    late_job=w.call("job_create",dict(project_id=pid,expected_version=1,record_ids=[r["id"] for r in late]))["job_id"]
    lease=w.call("claim",dict(job_id=late_job,limit=500))
    first=w.call("job_create",dict(project_id=pid,expected_version=1))["job_id"]
    while w.call("step",dict(job_id=first,limit=500))["state"]!="completed": pass
    with w.connect() as con:
        for row in rows:
            cur=w.current(con,pid,row["id"]); dec=w.decision(con,cur["decision"])
            assert dec["label"]==row["old"],"Template truth disagrees with executable v1"
    def review(version):
        for rid,label in human.items():
            with w.connect() as con: cur=w.current(con,pid,rid)
            w.call("review",dict(project_id=pid,record_id=rid,expected_version=version,expected_revision=cur["revision"],label=label,reviewer="simulated-reviewer",note="Synthetic benchmark event, not real human annotation"),human=True)
    review(1)
    before_change=time.perf_counter()
    preview=w.call("preview",dict(project_id=pid,rule=RULE2))
    selected={r["record_id"] for r in preview["items"] if not r["carry"]}
    w.call("activate",dict(project_id=pid,expected_version=1,rule=RULE2))
    job=w.call("job_create",dict(project_id=pid,expected_version=2))["job_id"]
    while w.call("step",dict(job_id=job,limit=500))["state"]!="completed": pass
    # Gate must refuse unresolved human decisions before confirmation.
    from dsh_label_workbench.service import Fault
    try: w.call("export",dict(project_id=pid,expected_version=2)); raise AssertionError("Unsafe early export")
    except Fault as e: assert e.code=="REVIEW_INCOMPLETE"
    review(2)
    callbacks=w.call("submit",dict(job_id=late_job,results=[dict(record_id=r["record_id"],token=r["token"],label=evaluate(RULE1,r["text"])) for r in lease["items"]]))
    with w.connect() as con:
        current={r["id"]:w.decision(con,w.current(con,pid,r["id"])["decision"]) for r in rows}
    out=w.call("export",dict(project_id=pid,expected_version=2))
    audit=verify_export(out["path"])
    changed={r["id"] for r in rows if r["old"]!=r["new"]}
    expected={r["id"]:human.get(r["id"],r["new"]) for r in rows}
    return dict(arm="workbench",missed_review=len(changed-selected),stale_current=sum(v["version"]!=2 for v in current.values()),human_overwritten=sum(current[rid]["label"]!=label or current[rid]["origin"]!="human" for rid,label in human.items()),wrong_final_labels=sum(current[rid]["label"]!=label for rid,label in expected.items()),new_machine_evaluations=len(selected),human_confirmations=len(human),isolated_callbacks=sum(x["disposition"]=="quarantined" for x in callbacks["outcomes"]),seconds=round(time.perf_counter()-start,6),change_and_export_seconds=round(time.perf_counter()-before_change,6),retained_human_history=audit["human_history_count"],scope_selected=len(selected),safe_carry=preview["carry_count"],artifact_verified=audit["valid"])

def main():
    evidence={"kind":"deterministic_synthetic_fault_injection","date":"2026-11-10","model_calls":0,"human_events":"simulated","protocol_sha256":hashlib.sha256((ROOT/"docs/PROTOCOL.md").read_bytes()).hexdigest(),"script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"runs":[],"limits":["Synthetic complete decision-list standards; not natural language impact prediction","Simple baseline intentionally lacks callback guards; guarded baseline also solves integrity","In-memory scripts and persistent SQLite/ZIP workbench timing are not a fair speed comparison","Human actions are scripted, not participant data"]}
    folder=ROOT/".verification/benchmark"; folder.mkdir(parents=True,exist_ok=True)
    for n,seed in [(120,7),(360,11),(1200,19)]:
        rows=dataset(n,seed)
        human={r["id"]:"其他" for r in rows[:max(3,n//30)]}
        late=rows[:min(100,n//5)]
        run={"n":n,"seed":seed,"changed_labels":sum(r["old"]!=r["new"] for r in rows),"late_callbacks":len(late),"human_records":len(human),"arms":[]}
        run["arms"].append(baseline(rows,human,late,False))
        run["arms"].append(baseline(rows,human,late,True))
        with tempfile.TemporaryDirectory(prefix="label-benchmark-") as store:
            run["arms"].append(plugin(rows,human,late,store))
        evidence["runs"].append(run)
        print(json.dumps(run,ensure_ascii=False),flush=True)
    (ROOT/"docs/benchmark-results.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    examples=ROOT/"examples"; examples.mkdir(exist_ok=True)
    for name,rule in [("rules-v1.json",RULE1),("rules-v2.json",RULE2)]:
        (examples/name).write_text(json.dumps(rule,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (examples/"support-records.json").write_text(json.dumps([{k:r[k] for k in ("id","text")} for r in dataset(24,7)],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__": main()
