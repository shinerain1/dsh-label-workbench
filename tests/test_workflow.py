import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dsh_label_workbench.service import Fault, Workbench, evaluate, verify_export
from dsh_label_workbench.review_ui import make_server

RULE = {"mode":"decision_list","labels":["refund","support","other"],"guidelines":"The ordered list is the complete labeling standard.","rules":[{"contains_any":["refund","return"],"label":"refund"}],"default":"other"}
NEW = {**RULE,"rules":[{"contains_any":["refund"],"label":"refund"},{"contains_any":["return","broken"],"label":"support"}]}
RECORDS=[{"id":"a","text":"Please return it"},{"id":"b","text":"refund please"},{"id":"c","text":"broken item"},{"id":"d","text":"hello"}]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.w=Workbench(self.temp.name)
        self.pid=self.w.call("create",dict(name="test",records=RECORDS,rule=RULE),"create",session_id="s")["project_id"]

    def tearDown(self): self.temp.cleanup()
    def job(self,version=1): return self.w.call("job_create",dict(project_id=self.pid,expected_version=version))["job_id"]
    def finish(self,job): return self.w.call("step",dict(job_id=job))
    def switch(self,rule=NEW): return self.w.call("activate",dict(project_id=self.pid,expected_version=1,rule=rule))
    def rows(self): return self.w.call("queue",dict(project_id=self.pid,pending_only=False))["items"]
    def review(self,rid="a",label="other",version=1):
        row=next(r for r in self.rows() if r["id"]==rid)
        return self.w.call("review",dict(project_id=self.pid,record_id=rid,expected_version=version,expected_revision=row["revision"],label=label,reviewer="test-human"),human=True)
    def assertFault(self,code,operation,**args):
        with self.assertRaises(Fault) as c: self.w.call(operation,args)
        self.assertEqual(c.exception.code,code)

    def test_version_switch_isolation_and_safe_carry(self):
        old=self.job()
        lease=self.w.call("claim",dict(job_id=old))
        self.finish(self.job())
        impact=self.w.call("preview",dict(project_id=self.pid,rule=NEW))
        self.assertEqual({r["record_id"] for r in impact["items"] if not r["carry"]},{"a","c"})
        self.assertEqual(self.switch()["carry_count"],2)
        reply=self.w.call("submit",dict(job_id=old,results=[dict(record_id=x["record_id"],token=x["token"],label="refund") for x in lease["items"]]))
        self.assertTrue(all(r["reason"]=="stale_rule" for r in reply["outcomes"]))
        self.finish(self.job(2))
        out=self.w.call("export",dict(project_id=self.pid,expected_version=2))
        self.assertTrue(verify_export(out["path"])["valid"])

    def test_human_review_is_not_overwritten_and_requires_confirmation(self):
        job=self.job()
        lease=self.w.call("claim",dict(job_id=job))
        self.review()
        reply=self.w.call("submit",dict(job_id=job,results=[dict(record_id=x["record_id"],token=x["token"],label="refund") for x in lease["items"]]))
        self.assertEqual(reply["outcomes"][0]["reason"],"record_changed")
        self.assertEqual(self.rows()[0]["decision"]["origin"],"human")
        self.switch()
        reply=self.finish(self.job(2))
        self.assertEqual(next(x for x in reply["outcomes"] if x["record_id"]=="a")["disposition"],"review_required")
        self.assertFault("REVIEW_INCOMPLETE","export",project_id=self.pid,expected_version=2)
        self.review(version=2,label="support")
        history=self.w.call("history",dict(project_id=self.pid,record_id="a"))["decisions"]
        self.assertEqual([x["version"] for x in history if x["origin"]=="human"],[1,2])
        self.assertTrue(verify_export(self.w.call("export",dict(project_id=self.pid,expected_version=2))["path"])["valid"])

    def test_natural_language_change_never_uses_heuristic_carry(self):
        self.finish(self.job())
        self.assertEqual(self.switch({"mode":"guidelines","labels":RULE["labels"],"guidelines":"Changed nuanced semantic policy"})["review_count"],4)

    def test_executable_wording_change_requires_all(self):
        self.finish(self.job())
        self.assertEqual(self.switch({**RULE,"guidelines":"Changed text"})["carry_count"],0)

    def test_wrong_previous_machine_label_is_not_carried(self):
        lease=self.w.call("claim",dict(job_id=self.job()))
        self.w.call("submit",dict(job_id=lease["job_id"],results=[dict(record_id=x["record_id"],token=x["token"],label="refund") for x in lease["items"]]))
        impact=self.w.call("preview",dict(project_id=self.pid,rule=NEW))
        self.assertFalse(next(r for r in impact["items"] if r["record_id"]=="d")["carry"])

    def test_stale_ui_version_and_revision_rejected(self):
        self.review()
        with self.assertRaises(Fault) as c:
            self.w.call("review",dict(project_id=self.pid,record_id="a",expected_version=1,expected_revision=0,label="refund",reviewer="x"),human=True)
        self.assertEqual(c.exception.code,"REVISION_CONFLICT")
        self.switch()
        with self.assertRaises(Fault) as c:
            self.w.call("review",dict(project_id=self.pid,record_id="a",expected_version=1,expected_revision=2,label="refund",reviewer="x"),human=True)
        self.assertEqual(c.exception.code,"VERSION_CONFLICT")

    def test_agent_cannot_call_human_review_rpc(self):
        self.assertFault("HUMAN_CHANNEL_REQUIRED","review",project_id=self.pid,record_id="a",expected_version=1,expected_revision=0,label="refund",reviewer="invented")

    def test_cancelled_callback_isolated(self):
        job=self.job(); lease=self.w.call("claim",dict(job_id=job))
        self.w.call("cancel",dict(job_id=job))
        x=lease["items"][0]
        reply=self.w.call("submit",dict(job_id=job,results=[dict(record_id=x["record_id"],token=x["token"],label="refund")]))
        self.assertEqual(reply["outcomes"][0]["reason"],"job_cancelled")
        self.assertIsNone(self.rows()[0]["decision"])

    def test_lease_expiry_reclaim_and_replay(self):
        job=self.job(); lease=self.w.call("claim",dict(job_id=job,limit=1,lease_seconds=1))
        time.sleep(1.05)
        new=self.w.call("claim",dict(job_id=job,limit=1))
        old=lease["items"][0]; x=new["items"][0]
        self.assertEqual(old["record_id"],x["record_id"])
        r=self.w.call("submit",dict(job_id=job,results=[dict(record_id=old["record_id"],token=old["token"],label="other")]))
        self.assertEqual(r["outcomes"][0]["reason"],"stale_lease")
        values=[dict(record_id=x["record_id"],token=x["token"],label="refund")]
        self.w.call("submit",dict(job_id=job,results=values))
        self.assertTrue(self.w.call("submit",dict(job_id=job,results=values))["outcomes"][0]["replayed"])
        self.assertFault("SUBMISSION_CONFLICT","submit",job_id=job,results=[{**values[0],"label":"other"}])

    def test_expired_callback_does_not_consume_recoverable_item(self):
        job=self.job(); lease=self.w.call("claim",dict(job_id=job,limit=1,lease_seconds=1))
        time.sleep(1.05); x=lease["items"][0]
        self.w.call("submit",dict(job_id=job,results=[dict(record_id=x["record_id"],token=x["token"],label="other")]))
        self.assertEqual(self.w.call("claim",dict(job_id=job,limit=1))["items"][0]["record_id"],x["record_id"])

    def test_batch_submission_rolls_back_on_invalid_label(self):
        job=self.job(); lease=self.w.call("claim",dict(job_id=job))
        values=[dict(record_id=x["record_id"],token=x["token"],label="refund" if i==0 else "invalid") for i,x in enumerate(lease["items"])]
        self.assertFault("INVALID_LABEL","submit",job_id=job,results=values)
        self.assertTrue(all(x["decision"] is None for x in self.rows()))

    def test_idempotency_conflict_and_restart(self):
        args=dict(name="test",records=RECORDS,rule=RULE)
        w=Workbench(self.temp.name)
        self.assertEqual(w.call("create",args,"create")["project_id"],self.pid)
        with self.assertRaises(Fault) as c: w.call("create",{**args,"name":"different"},"create")
        self.assertEqual(c.exception.code,"KEY_CONFLICT")
        self.assertEqual(w.call("list",{"session_id":"s"})["projects"][0]["id"],self.pid)

    def test_concurrent_claims_are_disjoint(self):
        job=self.job()
        with ThreadPoolExecutor(2) as pool:
            leases=list(pool.map(lambda _:Workbench(self.temp.name).call("claim",dict(job_id=job,limit=2)),range(2)))
        sets=[{x["record_id"] for x in l["items"]} for l in leases]
        self.assertEqual(len(sets[0]|sets[1]),4); self.assertFalse(sets[0]&sets[1])

    def test_real_delayed_process_cannot_mix_rules(self):
        job=self.job(); marker=Path(self.temp.name)/"claimed"
        proc=subprocess.Popen([sys.executable,"-m","dsh_label_workbench","--root",self.temp.name,"worker","--job",job,"--delay","1.2","--claimed-marker",str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8")
        try:
            deadline=time.monotonic()+15
            while not marker.exists() and time.monotonic()<deadline: time.sleep(.02)
            self.assertTrue(marker.exists()); self.switch()
            out,err=proc.communicate(timeout=20)
            self.assertEqual(proc.returncode,0,err)
            self.assertTrue(all(x["reason"]=="stale_rule" for x in json.loads(out)["batches"][0]["outcomes"]))
            self.assertTrue(all(x["decision"] is None for x in self.rows()))
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()

    def test_export_blocks_pending_active_jobs_and_mixed_version(self):
        self.assertFault("REVIEW_INCOMPLETE","export",project_id=self.pid,expected_version=1)
        lingering=self.job(); self.finish(self.job())
        self.assertFault("JOBS_ACTIVE","export",project_id=self.pid,expected_version=1)
        self.w.call("cancel",dict(job_id=lingering))
        self.assertFault("VERSION_CONFLICT","export",project_id=self.pid,expected_version=2)

    def test_export_tamper_detected(self):
        self.finish(self.job())
        result=self.w.call("export",dict(project_id=self.pid,expected_version=1))
        with zipfile.ZipFile(result["path"]) as z: files={n:z.read(n) for n in z.namelist()}
        files["data.jsonl"]=files["data.jsonl"].replace(b'"label":"refund"',b'"label":"other"',1)
        altered=Path(self.temp.name)/"altered.zip"
        with zipfile.ZipFile(altered,"w") as z:
            for name,value in files.items(): z.writestr(name,value)
        with self.assertRaises(Fault): verify_export(altered)

    def test_append_only_history(self):
        self.review()
        import sqlite3
        with self.w.connect() as con:
            with self.assertRaises(sqlite3.IntegrityError): con.execute("UPDATE decisions SET label='refund'")

    def test_label_studio_bundle_roundtrip_and_stale_import(self):
        bundle=self.w.call("review_bundle",dict(project_id=self.pid))
        tasks=json.loads((Path(bundle["directory"])/"tasks.json").read_text(encoding="utf-8"))
        task=tasks[0]
        task["annotations"]=[{"result":[{"from_name":"label","to_name":"text","type":"choices","value":{"choices":["support"]}}]}]
        self.w.call("review_import",dict(project_id=self.pid,tasks=[task],reviewer="local-importer"),human=True)
        self.assertEqual(self.rows()[0]["decision"]["origin"],"human")
        self.switch()
        with self.assertRaises(Fault) as c: self.w.call("review_import",dict(project_id=self.pid,tasks=[task],reviewer="local-importer"),human=True)
        self.assertEqual(c.exception.code,"VERSION_CONFLICT")

    def test_review_http_requires_token_and_rejects_stale_form(self):
        server,url=make_server(self.w,self.pid)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with urllib.request.urlopen(url) as r: self.assertIn("DSH".encode(),r.read())
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(url.split('?')[0])
            self.switch()
            endpoint=url.replace('/?','/review?')
            body=json.dumps(dict(record_id="a",expected_version=1,expected_revision=0,label="refund",reviewer="human")).encode()
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(urllib.request.Request(endpoint,data=body,headers={"Content-Type":"application/json"}))
            self.assertEqual(e.exception.code,409)
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_removed_label_is_history_not_invalid_label_studio_prediction(self):
        self.review(label="refund")
        self.switch({"mode":"guidelines","labels":["new-a","new-b"],"guidelines":"New vocabulary"})
        bundle=self.w.call("review_bundle",dict(project_id=self.pid))
        tasks=json.loads((Path(bundle["directory"])/"tasks.json").read_text(encoding="utf-8"))
        old=next(x for x in tasks if x["data"]["record_id"]=="a")
        self.assertEqual(old["predictions"],[])
        self.assertEqual(old["meta"]["historical_decision"]["label"],"refund")

    def test_valid_http_review_advances_and_preserves_audit(self):
        server,url=make_server(self.w,self.pid)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            body=json.dumps(dict(record_id="a",expected_version=1,expected_revision=0,label="refund",reviewer="simulated-ui-review",note="Automated interface test")).encode()
            req=urllib.request.Request(url.replace('/?','/review?'),data=body,headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req) as r: self.assertEqual(json.loads(r.read())["revision"],1)
            self.assertEqual(self.rows()[0]["decision"]["actor"],"simulated-ui-review")
            self.assertEqual(self.w.call("status",dict(project_id=self.pid))["pending_human"],0)
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_old_success_receipt_is_explicitly_historical_after_rule_change(self):
        job=self.job(); lease=self.w.call("claim",dict(job_id=job))
        values=[dict(record_id=x["record_id"],token=x["token"],label=evaluate(RULE,x["text"])) for x in lease["items"]]
        self.w.call("submit",dict(job_id=job,results=values),"submit-once")
        self.switch()
        original=self.w.call("submit",dict(job_id=job,results=values),"submit-once")
        self.assertTrue(original["request_replayed"])
        replay=self.w.call("submit",dict(job_id=job,results=values))
        self.assertEqual((replay["rule_version"],replay["active_rule_version"]),(1,2))
        self.assertTrue(all(x["replayed"] for x in replay["outcomes"]))
        self.assertEqual(self.w.call("status",dict(project_id=self.pid))["pending"],2)


if __name__=="__main__": unittest.main()
