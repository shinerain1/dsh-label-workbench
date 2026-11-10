"""Publish synthetic deliverables and generated baseline code, never raw model sessions."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
location=json.loads((root/".verification/agent-pilot-location.json").read_text(encoding="utf-8"))
pilot=Path(location["root"])
files={
    "plain-script":["records.json","rules-v1.json","rules-v2.json","state-v1.json","late-results.json","state-v2.json","result.json","resume.json","workflow.py","isolated-callbacks.json","callback-ledger.json"],
    "with-plugin":["state-v2.json","result.json","resume.json","impact-analysis.json","generate_state_v2.py"],
}
manifest=[]
for arm,names in files.items():
    source=pilot/arm; target=root/"docs/evidence"/arm; target.mkdir(parents=True,exist_ok=True)
    for name in names:
        raw=(source/name).read_text(encoding="utf-8")
        # Render every local project root as a descriptive placeholder in shared reports.
        clean=raw.replace(json.dumps(str(source))[1:-1],"<temporary-workspace>").replace(str(source),"<temporary-workspace>")
        require_no_secrets=not any(s in clean for s in (".credentials.yaml","DEEPSEEK_API_KEY","api.deepseek.com"))
        if not require_no_secrets: raise ValueError("Unexpected credential reference in deliverable")
        data=clean.encode("utf-8"); (target/name).write_bytes(data)
        manifest.append({"file":f"{arm}/{name}","sha256":hashlib.sha256(data).hexdigest(),"source_sha256":hashlib.sha256((source/name).read_bytes()).hexdigest(),"local_path_redacted":clean!=raw})
(root/"docs/evidence/manifest.json").write_text(json.dumps({"data":"synthetic","raw_model_sessions_included":False,"files":manifest},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"archived_synthetic_artifacts":len(manifest)}))
