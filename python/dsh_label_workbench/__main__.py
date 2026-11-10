"""CLI and versioned JSON RPC. Human review is deliberately absent from RPC."""
import argparse
import json
import sys
import time
from pathlib import Path
from . import __version__
from .service import Fault, Workbench, evaluate, require, verify_export


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".label-workbench")
    sub = p.add_subparsers(dest="command",required=True)
    sub.add_parser("rpc")
    call = sub.add_parser("call")
    call.add_argument("operation")
    call.add_argument("--input",required=True)
    call.add_argument("--key")
    create = sub.add_parser("import")
    create.add_argument("--name",required=True)
    create.add_argument("--data",required=True)
    create.add_argument("--rule",required=True)
    create.add_argument("--key",required=True)
    review = sub.add_parser("review")
    review.add_argument("--input",required=True,help="JSON with project_id, record_id, expected_version, expected_revision, label, reviewer, note")
    review.add_argument("--confirm-human-review",action="store_true",required=True)
    review_import = sub.add_parser("review-import")
    review_import.add_argument("--project",required=True)
    review_import.add_argument("--input",required=True)
    review_import.add_argument("--reviewer",required=True)
    review_import.add_argument("--confirm-human-review",action="store_true",required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("archive")
    worker = sub.add_parser("worker")
    worker.add_argument("--job",required=True)
    worker.add_argument("--delay",type=float,default=0)
    worker.add_argument("--batch-size",type=int,default=50)
    worker.add_argument("--claimed-marker",help="Optional marker for reproducible race tests")
    serve = sub.add_parser("serve-review")
    serve.add_argument("--project",required=True)
    serve.add_argument("--port",type=int,default=0)
    args = p.parse_args()
    if args.command == "verify":
        print(json.dumps(verify_export(args.archive)))
        return
    wb = Workbench(args.root)
    if args.command == "rpc":
        try:
            raw = sys.stdin.buffer.read(4*1024*1024+1)
            require(len(raw)<=4*1024*1024,"REQUEST_TOO_LARGE","RPC input limit 4 MiB")
            req=json.loads(raw)
            require(req.get("protocol_version")==1 and req.get("backend_version")==__version__,"PROTOCOL_VERSION","Plugin/backend version mismatch")
            context=req.get("dsh_context") or {}
            key=req.get("request_key")
            if key is None and context.get("session_id") and context.get("call_id"):
                key="dsh:"+context["session_id"]+":"+context["call_id"]
            result=wb.call(req["operation"],req.get("args",{}),key,context.get("session_id"))
            envelope={"protocol_version":1,"ok":True,"result":result}
        except (Fault,TypeError,KeyError,ValueError) as exc:
            envelope={"protocol_version":1,"ok":False,"error":{"code":getattr(exc,"code","INVALID_INPUT"),"message":str(exc)}}
        except Exception as exc:
            envelope={"protocol_version":1,"ok":False,"error":{"code":"BACKEND_ERROR","message":type(exc).__name__+": "+str(exc)}}
        print(json.dumps(envelope,ensure_ascii=False))
        return
    if args.command=="call":
        result=wb.call(args.operation,read_json(args.input),args.key)
    elif args.command=="import":
        path=Path(args.data)
        if path.suffix.lower()==".csv":
            import csv
            with path.open(encoding="utf-8-sig",newline="") as stream:
                records=list(csv.DictReader(stream))
        elif path.suffix.lower()==".jsonl":
            records=[json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        else:
            records=read_json(path)
        result=wb.call("create",dict(name=args.name,records=records,rule=read_json(args.rule)),args.key)
    elif args.command=="review":
        result=wb.call("review",read_json(args.input),human=True)
    elif args.command=="review-import":
        result=wb.call("review_import",dict(project_id=args.project,tasks=read_json(args.input),reviewer=args.reviewer),human=True)
    elif args.command=="worker":
        require(0<=args.delay<=300,"INVALID_INPUT","delay must be 0-300 seconds")
        results=[]
        while True:
            claimed=wb.call("claim",dict(job_id=args.job,limit=args.batch_size,lease_seconds=min(3600,int(args.delay)+120)))
            if not claimed["items"]:
                break
            require(claimed["rule"]["mode"]=="decision_list","NOT_EXECUTABLE","CLI demo worker only executes explicit decision lists; use DSH claim/submit for natural language")
            if args.claimed_marker:
                Path(args.claimed_marker).write_text("claimed",encoding="utf-8")
            values=[dict(record_id=x["record_id"],token=x["token"],label=evaluate(claimed["rule"],x["text"])) for x in claimed["items"]]
            if args.delay: time.sleep(args.delay)
            results.append(wb.call("submit",dict(job_id=args.job,results=values)))
        result={"batches":results,"job":wb.call("job_get",dict(job_id=args.job))}
    else:
        from .review_ui import serve
        serve(wb,args.project,args.port)
        return
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
