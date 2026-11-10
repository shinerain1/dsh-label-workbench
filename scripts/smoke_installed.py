"""Run an independent installed backend through rule change, human review and ZIP verification."""
import json
import tempfile
from pathlib import Path
import dsh_label_workbench
from dsh_label_workbench.service import Workbench, verify_export

assert "site-packages" in dsh_label_workbench.__file__,"Use a clean wheel-installed interpreter"
with tempfile.TemporaryDirectory(prefix="label-installed-") as root:
    w=Workbench(root)
    rule={"mode":"decision_list","labels":["a","b"],"guidelines":"Complete list","rules":[{"contains_any":["old"],"label":"a"}],"default":"b"}
    p=w.call("create",dict(name="installed",records=[{"id":"1","text":"old"},{"id":"2","text":"hello"}],rule=rule))["project_id"]
    j=w.call("job_create",dict(project_id=p,expected_version=1))["job_id"]; w.call("step",dict(job_id=j))
    w.call("review",dict(project_id=p,record_id="2",expected_version=1,expected_revision=1,label="a",reviewer="simulated-release-reviewer"),human=True)
    w.call("activate",dict(project_id=p,expected_version=1,rule={**rule,"rules":[]}))
    j=w.call("job_create",dict(project_id=p,expected_version=2))["job_id"]; w.call("step",dict(job_id=j))
    with w.connect() as con: revision=w.current(con,p,"2")["revision"]
    w.call("review",dict(project_id=p,record_id="2",expected_version=2,expected_revision=revision,label="b",reviewer="simulated-release-reviewer"),human=True)
    result=w.call("export",dict(project_id=p,expected_version=2))
    verified=verify_export(result["path"])
    assert verified["human_history_count"]==2 and verified["record_count"]==2
    print(json.dumps({"installed_wheel":True,"version":dsh_label_workbench.__version__,"export_verified":verified["valid"],"human_history_count":2}))
