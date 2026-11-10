"""Start an isolated synthetic review page for visual checks; never real human evidence."""
import json
import tempfile
from pathlib import Path
from benchmark import RULE1,RULE2,dataset
from dsh_label_workbench.service import Workbench
from dsh_label_workbench.review_ui import make_server

root=Path(__file__).resolve().parents[1]
store=Path(tempfile.mkdtemp(prefix="dsh-label-ui-"))
w=Workbench(store)
rows=dataset(12,7)
pid=w.call("create",dict(name="客服标注 · 页面验证样例",records=[{k:r[k] for k in ("id","text")} for r in rows],rule=RULE1))["project_id"]
job=w.call("job_create",dict(project_id=pid,expected_version=1))["job_id"]; w.call("step",dict(job_id=job))
w.call("activate",dict(project_id=pid,expected_version=1,rule=RULE2))
server,url=make_server(w,pid)
(root/".verification/ui-preview.json").write_text(json.dumps({"url":url,"store":str(store),"project_id":pid}),encoding="utf-8")
print(json.dumps({"url":url}),flush=True)
try: server.serve_forever()
except KeyboardInterrupt: pass
finally: server.server_close()
