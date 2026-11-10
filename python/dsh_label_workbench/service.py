from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import __version__


class Fault(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise Fault(code, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def ident():
    return uuid.uuid4().hex


def validate_rule(rule):
    require(isinstance(rule, dict), "INVALID_RULE", "rule must be an object")
    require(set(rule) <= {"mode", "labels", "guidelines", "rules", "default"}, "INVALID_RULE", "Unknown rule fields")
    labels = rule.get("labels")
    require(isinstance(labels, list) and 1 < len(labels) <= 100 and all(isinstance(x, str) and 0 < len(x) <= 100 for x in labels), "INVALID_RULE", "Provide 2-100 nonempty labels")
    require(len(set(labels)) == len(labels), "INVALID_RULE", "Labels must be unique")
    require(rule.get("mode") in {"guidelines", "decision_list"}, "INVALID_RULE", "mode is guidelines or decision_list")
    require(isinstance(rule.get("guidelines"), str) and len(rule["guidelines"]) <= 20000, "INVALID_RULE", "guidelines string required")
    if rule["mode"] == "decision_list":
        require(rule.get("default") in labels, "INVALID_RULE", "Default must be a label")
        require(isinstance(rule.get("rules"), list) and len(rule["rules"]) <= 100, "INVALID_RULE", "At most 100 ordered rules")
        for clause in rule["rules"]:
            require(isinstance(clause, dict) and set(clause) == {"contains_any", "label"}, "INVALID_RULE", "Each rule needs contains_any and label")
            terms = clause["contains_any"]
            require(isinstance(terms, list) and 0 < len(terms) <= 100 and all(isinstance(t, str) and 0 < len(t) <= 200 for t in terms), "INVALID_RULE", "Use nonempty literal substrings")
            require(clause["label"] in labels, "INVALID_RULE", "Clause label not in vocabulary")
    else:
        require(not ({"default", "rules"} & set(rule)), "INVALID_RULE", "Natural language mode cannot claim executable rules")
    return json.loads(canonical(rule))


def evaluate(rule, text):
    require(rule["mode"] == "decision_list", "NOT_EXECUTABLE", "Natural language guidelines need an external annotator")
    folded = text.casefold()
    for clause in rule["rules"]:
        if any(t.casefold() in folded for t in clause["contains_any"]):
            return clause["label"]
    return rule["default"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rules(project TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(project,version));
CREATE TABLE IF NOT EXISTS records(project TEXT NOT NULL,id TEXT NOT NULL,text TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(project,id));
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,project TEXT NOT NULL,record TEXT NOT NULL,version INTEGER NOT NULL,label TEXT NOT NULL,origin TEXT NOT NULL,parent TEXT,actor TEXT NOT NULL,note TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS current(project TEXT NOT NULL,record TEXT NOT NULL,decision TEXT,needs_review INTEGER NOT NULL,requires_human INTEGER NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(project,record));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL,version INTEGER NOT NULL,rule_hash TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS items(job TEXT NOT NULL,record TEXT NOT NULL,content_hash TEXT NOT NULL,base_revision INTEGER NOT NULL,state TEXT NOT NULL,token TEXT,expires REAL,PRIMARY KEY(job,record));
CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,job TEXT NOT NULL,record TEXT NOT NULL,token TEXT NOT NULL,payload_hash TEXT NOT NULL,label TEXT NOT NULL,disposition TEXT NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL,UNIQUE(job,record,token));
CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,hash TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(session TEXT NOT NULL,project TEXT NOT NULL,PRIMARY KEY(session,project));
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,previous TEXT NOT NULL,hash TEXT NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_project ON events(project,seq);
CREATE INDEX IF NOT EXISTS decisions_record ON decisions(project,record);
CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project);
"""


class Workbench:
    """Local single-user store. Transactions serialize version switches and worker commits."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "workbench.sqlite3"
        with self.connect() as con:
            con.executescript(SCHEMA)
            con.execute("INSERT INTO meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM meta)")
            require(con.execute("SELECT version FROM meta").fetchone()[0] == 1, "SCHEMA_VERSION", "Unsupported database schema")
            for table in ("rules", "records", "decisions", "results", "events"):
                for action in ("UPDATE", "DELETE"):
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only history'); END")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA journal_mode=WAL")
            with con:
                yield con
        finally:
            con.close()

    def event(self, con, project, kind, payload):
        row = con.execute("SELECT hash FROM events WHERE project=? ORDER BY seq DESC LIMIT 1", (project,)).fetchone()
        previous = row[0] if row else "0" * 64
        stamp = time.time()
        value = dict(project=project, kind=kind, payload=payload, previous=previous, created=stamp)
        con.execute("INSERT INTO events(project,kind,payload,previous,hash,created) VALUES(?,?,?,?,?,?)", (project,kind,canonical(payload),previous,digest(value),stamp))

    def project(self, con, pid):
        row = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        require(row is not None, "NOT_FOUND", "Unknown project")
        return dict(row)

    def rule(self, con, pid, version):
        row = con.execute("SELECT * FROM rules WHERE project=? AND version=?", (pid,version)).fetchone()
        require(row is not None, "NOT_FOUND", "Unknown rule version")
        require(digest(json.loads(row["payload"])) == row["hash"], "INTEGRITY", "Rule hash mismatch")
        return json.loads(row["payload"])

    def current(self, con, pid, rid):
        row = con.execute("SELECT * FROM current WHERE project=? AND record=?", (pid,rid)).fetchone()
        require(row is not None, "NOT_FOUND", "Unknown record")
        return dict(row)

    def decision(self, con, did):
        if did is None:
            return None
        row = con.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
        require(row is not None, "INTEGRITY", "Missing decision")
        return dict(row)

    def add_decision(self, con, pid, rid, version, label, origin, parent, actor, note):
        did = ident()
        con.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)", (did,pid,rid,version,label,origin,parent,actor,note,time.time()))
        return did

    def call(self, operation, args=None, request_key=None, session_id=None, human=False):
        args = dict(args or {})
        require(operation in OPERATIONS, "UNKNOWN_OPERATION", "Unknown operation")
        require(operation not in {"review", "review_import"} or human, "HUMAN_CHANNEL_REQUIRED", "Human review is available only through the local reviewer CLI/UI")
        request_hash = digest({"operation": operation, "args": args, "human": human})
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if request_key and operation in MUTATIONS:
                require(isinstance(request_key, str) and len(request_key) <= 300, "INVALID_KEY", "Invalid request key")
                old = con.execute("SELECT * FROM requests WHERE key=?", (request_key,)).fetchone()
                if old:
                    require(old["hash"] == request_hash, "KEY_CONFLICT", "A request key cannot be reused with different arguments")
                    result = json.loads(old["result"])
                    if session_id and result.get("project_id"):
                        con.execute("INSERT OR IGNORE INTO sessions VALUES(?,?)", (session_id,result["project_id"]))
                    return {**result,"request_replayed":True,"observation":"Historical mutation receipt; read project/job status for current state"}
            method = getattr(self, "op_" + operation)
            result = method(con, **args)
            pid = result.get("project_id", args.get("project_id"))
            if session_id and pid:
                con.execute("INSERT OR IGNORE INTO sessions VALUES(?,?)", (session_id,pid))
            if request_key and operation in MUTATIONS:
                con.execute("INSERT INTO requests VALUES(?,?,?)", (request_key,request_hash,canonical(result)))
            return result

    def op_health(self, con):
        return {"backend_version": __version__, "schema_version": 1, "sqlite": sqlite3.sqlite_version, "root": str(self.root)}

    def op_create(self, con, name, records, rule):
        rule = validate_rule(rule)
        require(isinstance(name,str) and 0 < len(name) <= 200, "INVALID_INPUT", "Project name required")
        require(isinstance(records,list) and 0 < len(records) <= 20000, "INVALID_INPUT", "Import 1-20000 immutable text records")
        ids = set()
        prepared = []
        for row in records:
            require(isinstance(row,dict) and set(row) == {"id","text"}, "INVALID_INPUT", "Each record contains only id and text")
            require(isinstance(row["id"],str) and 0 < len(row["id"]) <= 200 and row["id"] not in ids, "INVALID_INPUT", "Unique nonempty string record IDs required")
            require(isinstance(row["text"],str) and len(row["text"]) <= 50000, "INVALID_INPUT", "Text at most 50000 characters")
            ids.add(row["id"])
            prepared.append((row["id"],row["text"],digest(row)))
        pid = ident()
        con.execute("INSERT INTO projects VALUES(?,?,1,?)", (pid,name,time.time()))
        con.execute("INSERT INTO rules VALUES(?,1,?,?)", (pid,canonical(rule),digest(rule)))
        con.executemany("INSERT INTO records VALUES(?,?,?,?)", [(pid,*r) for r in prepared])
        con.executemany("INSERT INTO current VALUES(?,?,NULL,1,0,0)", [(pid,r[0]) for r in prepared])
        self.event(con,pid,"project_created",dict(name=name,rule_hash=digest(rule),record_hash=digest(prepared),count=len(records)))
        return {"project_id":pid,"rule_version":1,"record_count":len(records)}

    def op_status(self, con, project_id):
        p = self.project(con,project_id)
        counts = con.execute("SELECT COUNT(*) total,SUM(needs_review) pending,SUM(requires_human) human_protected,SUM(needs_review*requires_human) pending_human FROM current WHERE project=?", (project_id,)).fetchone()
        jobs = [dict(r) for r in con.execute("SELECT id,version,state FROM jobs WHERE project=? ORDER BY created", (project_id,))]
        quarantined = con.execute("SELECT COUNT(*) FROM results r JOIN jobs j ON j.id=r.job WHERE j.project=? AND disposition!='applied'", (project_id,)).fetchone()[0]
        return {"project_id":project_id,"name":p["name"],"rule_version":p["active"],"rule":self.rule(con,project_id,p["active"]),**dict(counts),"jobs":jobs,"isolated_results":quarantined}

    def op_list(self, con, session_id=None):
        query = "SELECT p.* FROM projects p"
        args = ()
        if session_id:
            query += " JOIN sessions s ON s.project=p.id WHERE s.session=?"
            args = (session_id,)
        return {"projects":[dict(r) for r in con.execute(query + " ORDER BY p.created DESC",args)]}

    def impact(self, con, project_id, rule):
        p = self.project(con,project_id)
        old = self.rule(con,project_id,p["active"])
        # Wording changes cannot be proved harmless by a substring engine.
        selective = old["mode"] == rule["mode"] == "decision_list" and old["guidelines"] == rule["guidelines"]
        rows = []
        for row in con.execute("SELECT r.id,r.text,c.* FROM records r JOIN current c ON r.project=c.project AND r.id=c.record WHERE r.project=? ORDER BY r.id", (project_id,)):
            dec = self.decision(con,row["decision"])
            reason = "guidelines_or_mode_changed"
            carry = False
            if row["requires_human"] or (dec and dec["origin"] == "human"):
                reason = "human_confirmation_required"
            elif row["needs_review"] or not dec:
                reason = "no_current_valid_decision"
            elif selective:
                before, after = evaluate(old,row["text"]), evaluate(rule,row["text"])
                carry = before == after == dec["label"] and dec["label"] in rule["labels"]
                reason = "executable_outcome_unchanged" if carry else "outcome_or_previous_label_changed"
            rows.append({"record_id":row["id"],"carry":carry,"reason":reason})
        return p, rows

    def op_preview(self, con, project_id, rule):
        rule = validate_rule(rule)
        p, rows = self.impact(con,project_id,rule)
        return {"project_id":project_id,"from_version":p["active"],"rule_hash":digest(rule),"review_count":sum(not r["carry"] for r in rows),"carry_count":sum(r["carry"] for r in rows),"items":rows}

    def op_activate(self, con, project_id, rule, expected_version):
        rule = validate_rule(rule)
        p, rows = self.impact(con,project_id,rule)
        require(p["active"] == expected_version, "VERSION_CONFLICT", "Rule changed after preview; preview again")
        require(digest(rule) != digest(self.rule(con,project_id,p["active"])), "UNCHANGED_RULE", "No rule changes")
        version = p["active"] + 1
        con.execute("INSERT INTO rules VALUES(?,?,?,?)", (project_id,version,canonical(rule),digest(rule)))
        for item in rows:
            cur = self.current(con,project_id,item["record_id"])
            dec = self.decision(con,cur["decision"])
            if item["carry"]:
                did = self.add_decision(con,project_id,item["record_id"],version,dec["label"],"carry",dec["id"],"rule-engine","Exact executable outcomes agree")
                con.execute("UPDATE current SET decision=?,needs_review=0,revision=revision+1 WHERE project=? AND record=?", (did,project_id,item["record_id"]))
            else:
                human = cur["requires_human"] or bool(dec and dec["origin"] == "human")
                con.execute("UPDATE current SET needs_review=1,requires_human=?,revision=revision+1 WHERE project=? AND record=?", (int(human),project_id,item["record_id"]))
        con.execute("UPDATE projects SET active=? WHERE id=?", (version,project_id))
        superseded = con.execute("UPDATE jobs SET state='superseded' WHERE project=? AND state IN ('queued','running')", (project_id,)).rowcount
        self.event(con,project_id,"rule_activated",dict(version=version,rule_hash=digest(rule),impact=rows,superseded=superseded))
        return {"project_id":project_id,"rule_version":version,"review_count":sum(not r["carry"] for r in rows),"carry_count":sum(r["carry"] for r in rows),"superseded_jobs":superseded}

    def op_queue(self, con, project_id, pending_only=True, offset=0, limit=100):
        p = self.project(con,project_id)
        require(isinstance(limit,int) and 1 <= limit <= 500 and isinstance(offset,int) and offset >= 0, "INVALID_INPUT", "Pagination: limit 1-500, offset >=0")
        condition = " AND c.needs_review=1" if pending_only else ""
        rows = []
        for row in con.execute("SELECT r.id,r.text,r.hash,c.* FROM records r JOIN current c ON r.project=c.project AND r.id=c.record WHERE r.project=?" + condition + " ORDER BY r.id LIMIT ? OFFSET ?", (project_id,limit,offset)):
            item = dict(row)
            item["decision"] = self.decision(con,item["decision"])
            item["suggestions"] = [dict(x) for x in con.execute("SELECT r.label,r.reason,j.version FROM results r JOIN jobs j ON j.id=r.job WHERE j.project=? AND r.record=? AND r.disposition='review_required' ORDER BY r.created DESC LIMIT 5", (project_id,row["id"]))]
            rows.append(item)
        return {"project_id":project_id,"rule_version":p["active"],"items":rows,"next_offset":offset+len(rows) if len(rows)==limit else None}

    def op_job_create(self, con, project_id, expected_version, record_ids=None):
        p = self.project(con,project_id)
        require(p["active"] == expected_version, "VERSION_CONFLICT", "Active rule differs")
        rows = con.execute("SELECT r.id,r.hash,c.revision FROM records r JOIN current c ON c.project=r.project AND c.record=r.id WHERE r.project=? AND c.needs_review=1 ORDER BY r.id", (project_id,)).fetchall()
        if record_ids is not None:
            require(isinstance(record_ids,list) and all(isinstance(x,str) for x in record_ids) and len(set(record_ids))==len(record_ids), "INVALID_INPUT", "record_ids must be distinct strings")
            known = {r["id"] for r in rows}
            require(set(record_ids) <= known, "NOT_PENDING", "Only currently pending records may be scheduled")
            rows = [r for r in rows if r["id"] in record_ids]
        require(rows, "NOTHING_TO_DO", "No pending records")
        jid = ident()
        con.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (jid,project_id,p["active"],digest(self.rule(con,project_id,p["active"])),"queued",time.time()))
        con.executemany("INSERT INTO items VALUES(?,?,?,?, 'pending',NULL,NULL)", [(jid,r["id"],r["hash"],r["revision"]) for r in rows])
        self.event(con,project_id,"job_created",dict(job_id=jid,version=p["active"],count=len(rows)))
        return {"project_id":project_id,"job_id":jid,"rule_version":p["active"],"count":len(rows),"state":"queued"}

    def job(self, con, jid):
        row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        require(row is not None, "NOT_FOUND", "Unknown job")
        return dict(row)

    def op_claim(self, con, job_id, limit=50, lease_seconds=120):
        j = self.job(con,job_id)
        require(isinstance(limit,int) and 1 <= limit <= 500 and isinstance(lease_seconds,int) and 1 <= lease_seconds <= 3600, "INVALID_INPUT", "Invalid claim bounds")
        if j["state"] not in {"queued","running"}:
            return {"project_id":j["project"],"job_id":job_id,"state":j["state"],"items":[]}
        require(self.project(con,j["project"])["active"] == j["version"], "VERSION_CONFLICT", "Job is stale")
        now = time.time()
        rows = con.execute("SELECT * FROM items WHERE job=? AND (state='pending' OR (state='leased' AND expires<=?)) ORDER BY record LIMIT ?", (job_id,now,limit)).fetchall()
        items = []
        for row in rows:
            token = ident()
            con.execute("UPDATE items SET state='leased',token=?,expires=? WHERE job=? AND record=?", (token,now+lease_seconds,job_id,row["record"]))
            rec = con.execute("SELECT text FROM records WHERE project=? AND id=?", (j["project"],row["record"])).fetchone()
            items.append({"record_id":row["record"],"text":rec["text"],"content_hash":row["content_hash"],"token":token,"expires":now+lease_seconds})
        con.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
        self.event(con,j["project"],"items_claimed",dict(job_id=job_id,records=[r["record_id"] for r in items]))
        return {"project_id":j["project"],"job_id":job_id,"rule_version":j["version"],"rule":self.rule(con,j["project"],j["version"]),"state":"running","items":items}

    def op_submit(self, con, job_id, results):
        j = self.job(con,job_id)
        require(isinstance(results,list) and 1 <= len(results) <= 500, "INVALID_INPUT", "Submit 1-500 results")
        rule = self.rule(con,j["project"],j["version"])
        active = self.project(con,j["project"])["active"]
        outcomes = []
        for value in results:
            require(isinstance(value,dict) and set(value)=={"record_id","token","label"}, "INVALID_INPUT", "Expected record_id, token, label only")
            rid, token, label = value["record_id"],value["token"],value["label"]
            require(all(isinstance(x,str) for x in (rid,token,label)), "INVALID_INPUT", "Result fields are strings")
            require(label in rule["labels"], "INVALID_LABEL", "Label outside the job's frozen vocabulary")
            ph = digest(value)
            old = con.execute("SELECT * FROM results WHERE job=? AND record=? AND token=?", (job_id,rid,token)).fetchone()
            if old:
                require(old["payload_hash"]==ph, "SUBMISSION_CONFLICT", "A lease result is immutable")
                outcomes.append({"record_id":rid,"disposition":old["disposition"],"reason":old["reason"],"replayed":True})
                continue
            item = con.execute("SELECT * FROM items WHERE job=? AND record=?", (job_id,rid)).fetchone()
            require(item is not None, "NOT_FOUND", "Record not scheduled by this job")
            cur = self.current(con,j["project"],rid)
            reason = ""
            disposition = "quarantined"
            if j["version"] != active:
                reason = "stale_rule"
            elif j["state"] not in {"queued","running"}:
                reason = "job_" + j["state"]
            elif item["token"] != token or item["state"] != "leased" or item["expires"] <= time.time():
                reason = "stale_lease"
            elif cur["revision"] != item["base_revision"]:
                reason = "record_changed"
            elif cur["requires_human"]:
                disposition,reason = "review_required","human_confirmation_required"
            else:
                disposition,reason = "applied","current_rule_and_revision"
                did = self.add_decision(con,j["project"],rid,active,label,"machine",cur["decision"],job_id,"Worker suggestion accepted")
                con.execute("UPDATE current SET decision=?,needs_review=0,revision=revision+1 WHERE project=? AND record=?", (did,j["project"],rid))
            con.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)", (ident(),job_id,rid,token,ph,label,disposition,reason,time.time()))
            if item["token"] == token and item["state"] == "leased" and reason != "stale_lease":
                con.execute("UPDATE items SET state='done' WHERE job=? AND record=?", (job_id,rid))
            outcomes.append({"record_id":rid,"disposition":disposition,"reason":reason,"replayed":False})
        if j["state"] in {"queued","running"} and not con.execute("SELECT 1 FROM items WHERE job=? AND state!='done' LIMIT 1", (job_id,)).fetchone():
            con.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_id,))
        self.event(con,j["project"],"results_submitted",dict(job_id=job_id,outcomes=outcomes))
        return {"project_id":j["project"],"job_id":job_id,"rule_version":j["version"],"active_rule_version":active,"state":self.job(con,job_id)["state"],"outcomes":outcomes}

    def op_job_get(self, con, job_id):
        j = self.job(con,job_id)
        counts = {r[0]:r[1] for r in con.execute("SELECT state,COUNT(*) FROM items WHERE job=? GROUP BY state", (job_id,))}
        return {"project_id":j["project"],"job_id":job_id,"rule_version":j["version"],"state":j["state"],"items":counts}

    def op_step(self, con, job_id, limit=50):
        j = self.job(con,job_id)
        rule = self.rule(con,j["project"],j["version"])
        require(rule["mode"]=="decision_list", "NOT_EXECUTABLE", "Use claim/submit with DSH for natural language guidelines")
        claim = self.op_claim(con,job_id,limit)
        if not claim["items"]:
            return {**self.op_job_get(con,job_id),"outcomes":[]}
        values = [{"record_id":r["record_id"],"token":r["token"],"label":evaluate(rule,r["text"])} for r in claim["items"]]
        return self.op_submit(con,job_id,values)

    def op_cancel(self, con, job_id):
        j = self.job(con,job_id)
        if j["state"] in {"queued","running"}:
            con.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job_id,))
            self.event(con,j["project"],"job_cancelled",dict(job_id=job_id))
        return self.op_job_get(con,job_id)

    def op_review(self, con, project_id, record_id, expected_version, expected_revision, label, reviewer, note=""):
        p = self.project(con,project_id)
        require(p["active"] == expected_version, "VERSION_CONFLICT", "Review page is from an old rule version")
        cur = self.current(con,project_id,record_id)
        require(cur["revision"] == expected_revision, "REVISION_CONFLICT", "Another decision arrived; reload before reviewing")
        require(label in self.rule(con,project_id,p["active"])["labels"], "INVALID_LABEL", "Unknown label")
        require(isinstance(reviewer,str) and 0 < len(reviewer.strip()) <= 200, "INVALID_INPUT", "Reviewer name required")
        require(isinstance(note,str) and len(note) <= 5000, "INVALID_INPUT", "Review note too long")
        did = self.add_decision(con,project_id,record_id,p["active"],label,"human",cur["decision"],reviewer,note)
        con.execute("UPDATE current SET decision=?,needs_review=0,requires_human=1,revision=revision+1 WHERE project=? AND record=?", (did,project_id,record_id))
        self.event(con,project_id,"human_review",dict(record_id=record_id,decision_id=did,version=p["active"],reviewer=reviewer))
        return {"project_id":project_id,"record_id":record_id,"decision_id":did,"rule_version":p["active"],"revision":cur["revision"]+1}

    def op_history(self, con, project_id, record_id):
        self.current(con,project_id,record_id)
        return {"project_id":project_id,"record_id":record_id,"decisions":[dict(r) for r in con.execute("SELECT * FROM decisions WHERE project=? AND record=? ORDER BY created,id", (project_id,record_id))]}

    def op_review_bundle(self, con, project_id):
        p = self.project(con,project_id)
        labels = self.rule(con,project_id,p["active"])["labels"]
        tasks = []
        for index,row in enumerate(con.execute("SELECT r.*,c.revision,c.decision FROM records r JOIN current c ON c.project=r.project AND c.record=r.id WHERE r.project=? AND c.needs_review=1 ORDER BY r.id", (project_id,)),1):
            dec = self.decision(con,row["decision"])
            tasks.append({"id":index,"data":{"text":row["text"],"record_id":row["id"]},"meta":{"project_id":project_id,"rule_version":p["active"],"content_hash":row["hash"],"revision":row["revision"],"historical_decision":dec},"predictions":[] if not dec or dec["label"] not in labels else [{"model_version":f"historical-rule-{dec['version']}","result":[{"from_name":"label","to_name":"text","type":"choices","value":{"choices":[dec["label"]]}}]}]})
        folder = self.root / "review-bundles" / ident()
        folder.mkdir(parents=True)
        (folder/"tasks.json").write_text(json.dumps(tasks,ensure_ascii=False,indent=2),encoding="utf-8")
        from xml.sax.saxutils import quoteattr
        config = '<View><Text name="text" value="$text"/><Choices name="label" toName="text" choice="single">' + ''.join('<Choice value='+quoteattr(x)+'/>' for x in labels) + '</Choices></View>'
        (folder/"label-config.xml").write_text(config,encoding="utf-8")
        (folder/"rule.json").write_text(canonical(self.rule(con,project_id,p["active"])),encoding="utf-8")
        return {"project_id":project_id,"rule_version":p["active"],"task_count":len(tasks),"directory":str(folder)}

    def op_review_import(self, con, project_id, tasks, reviewer):
        require(isinstance(tasks,list) and 0 < len(tasks) <= 20000, "INVALID_INPUT", "Expected Label Studio JSON tasks")
        outputs = []
        for task in tasks:
            meta = task.get("meta",{})
            data = task.get("data",{})
            require(meta.get("project_id") == project_id, "PROJECT_CONFLICT", "Foreign task")
            rec = con.execute("SELECT * FROM records WHERE project=? AND id=?", (project_id,data.get("record_id"))).fetchone()
            require(rec is not None and meta.get("content_hash")==rec["hash"] and data.get("text")==rec["text"], "CONTENT_CONFLICT", "Record contents changed")
            annotations = [a for a in task.get("annotations",[]) if not a.get("was_cancelled",False)]
            require(len(annotations)==1, "AMBIGUOUS_REVIEW", "Exactly one completed annotation per imported task; adjudicate multiple reviews first")
            values = annotations[0].get("result",[])
            require(len(values)==1 and values[0].get("from_name")=="label" and values[0].get("to_name")=="text" and values[0].get("type")=="choices" and len(values[0].get("value",{}).get("choices",[]))==1, "INVALID_REVIEW", "Expected one single-label choices annotation")
            outputs.append(self.op_review(con,project_id,data["record_id"],meta.get("rule_version"),meta.get("revision"),values[0]["value"]["choices"][0],reviewer,"Imported from explicitly confirmed Label Studio review file"))
        return {"project_id":project_id,"imported":len(outputs),"reviews":outputs}

    def op_export(self, con, project_id, expected_version):
        p = self.project(con,project_id)
        require(p["active"]==expected_version, "VERSION_CONFLICT", "Export requires the expected active rule version")
        pending = con.execute("SELECT COUNT(*) FROM current WHERE project=? AND needs_review=1", (project_id,)).fetchone()[0]
        require(pending==0, "REVIEW_INCOMPLETE", f"{pending} records still need work/review")
        require(not con.execute("SELECT 1 FROM jobs WHERE project=? AND state IN ('queued','running') LIMIT 1", (project_id,)).fetchone(), "JOBS_ACTIVE", "Finish or cancel active jobs before export")
        rule = self.rule(con,project_id,p["active"])
        rows = []
        for rec in con.execute("SELECT r.*,c.decision FROM records r JOIN current c ON c.project=r.project AND c.record=r.id WHERE r.project=? ORDER BY r.id", (project_id,)):
            require(digest({"id":rec["id"],"text":rec["text"]})==rec["hash"], "INTEGRITY", "Content hash mismatch")
            dec = self.decision(con,rec["decision"])
            require(dec and dec["version"]==p["active"] and dec["label"] in rule["labels"], "INTEGRITY", "Current decision is not valid for active rule")
            rows.append({"id":rec["id"],"text":rec["text"],"content_hash":rec["hash"],"label":dec["label"],"rule_version":p["active"],"decision_id":dec["id"],"origin":dec["origin"]})
        rules = [dict(r) for r in con.execute("SELECT * FROM rules WHERE project=? ORDER BY version", (project_id,))]
        decisions = [dict(r) for r in con.execute("SELECT * FROM decisions WHERE project=? ORDER BY created,id", (project_id,))]
        events = [dict(r) for r in con.execute("SELECT * FROM events WHERE project=? ORDER BY seq", (project_id,))]
        verify_events(events,project_id)
        files = {"data.jsonl":(''.join(canonical(r)+'\n' for r in rows)).encode("utf-8"), "rules.json":canonical(rules).encode("utf-8"), "decisions.json":canonical(decisions).encode("utf-8"), "events.json":canonical(events).encode("utf-8")}
        manifest = {"format":"dsh-label-workbench/1","backend_version":__version__,"project_id":project_id,"rule_version":p["active"],"rule_hash":digest(rule),"record_count":len(rows),"created":time.time(),"sha256":{k:hashlib.sha256(v).hexdigest() for k,v in files.items()},"audit_head":events[-1]["hash"] if events else None}
        path = self.root / "exports" / (ident()+".zip")
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            with zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED) as z:
                for name,data in files.items(): z.writestr(name,data)
                z.writestr("manifest.json",canonical(manifest))
            verify_export(tmp)
            os.replace(tmp,path)
        finally:
            if tmp.exists(): tmp.unlink()
        self.event(con,project_id,"dataset_exported",dict(filename=path.name,manifest_hash=digest(manifest),rule_version=p["active"]))
        return {"project_id":project_id,"rule_version":p["active"],"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"record_count":len(rows)}


def verify_events(events, project):
    previous = "0"*64
    for event in events:
        value = {k:event[k] for k in ("project","kind","previous","created")}
        value["payload"] = json.loads(event["payload"])
        require(event["project"]==project and event["previous"]==previous and digest(value)==event["hash"], "INTEGRITY", "Broken audit chain")
        previous = event["hash"]


def verify_export(path):
    with zipfile.ZipFile(path) as z:
        expected = {"manifest.json","data.jsonl","rules.json","decisions.json","events.json"}
        require(set(z.namelist())==expected and len(z.namelist())==len(expected), "INTEGRITY", "Unexpected or duplicate archive members")
        require(sum(x.file_size for x in z.infolist()) <= 128*1024*1024, "TOO_LARGE", "Archive exceeds 128 MiB")
        manifest = json.loads(z.read("manifest.json"))
        require(manifest["format"]=="dsh-label-workbench/1", "INTEGRITY", "Unknown export format")
        require(set(manifest["sha256"])==expected-{"manifest.json"}, "INTEGRITY", "Incomplete checksums")
        for name,sha in manifest["sha256"].items():
            require(hashlib.sha256(z.read(name)).hexdigest()==sha, "INTEGRITY", "Artifact checksum mismatch")
        rules = json.loads(z.read("rules.json"))
        decisions = json.loads(z.read("decisions.json"))
        events = json.loads(z.read("events.json"))
        rows = [json.loads(line) for line in z.read("data.jsonl").decode("utf-8").splitlines()]
        project = manifest["project_id"]
        verify_events(events,project)
        require(events and events[-1]["hash"]==manifest["audit_head"], "INTEGRITY", "Wrong audit head")
        rulemap = {}
        for rule in rules:
            payload = validate_rule(json.loads(rule["payload"]))
            require(rule["project"]==project and digest(payload)==rule["hash"], "INTEGRITY", "Rule integrity failed")
            rulemap[rule["version"]] = payload
        require(digest(rulemap[manifest["rule_version"]])==manifest["rule_hash"], "INTEGRITY", "Active rule hash mismatch")
        decmap = {d["id"]:d for d in decisions}
        require(len(decmap)==len(decisions), "INTEGRITY", "Duplicate decision IDs")
        for dec in decisions:
            require(dec["project"]==project and dec["label"] in rulemap[dec["version"]]["labels"] and dec["origin"] in {"human","machine","carry"}, "INTEGRITY", "Invalid decision")
            if dec["parent"]:
                parent = decmap.get(dec["parent"])
                require(parent and parent["record"]==dec["record"] and parent["created"] <= dec["created"], "INTEGRITY", "Invalid lineage")
        require(len(rows)==manifest["record_count"] and len({r["id"] for r in rows})==len(rows), "INTEGRITY", "Row count/IDs mismatch")
        for row in rows:
            dec = decmap.get(row["decision_id"])
            require(dec and dec["record"]==row["id"] and dec["label"]==row["label"] and dec["origin"]==row["origin"] and dec["version"]==row["rule_version"]==manifest["rule_version"], "INTEGRITY", "Row does not match provenance")
            require(digest({"id":row["id"],"text":row["text"]})==row["content_hash"], "INTEGRITY", "Data hash mismatch")
            if dec["origin"]=="carry":
                parent=decmap[dec["parent"]]
                before,after=rulemap[parent["version"]],rulemap[dec["version"]]
                require(before["mode"]==after["mode"]=="decision_list" and before["guidelines"]==after["guidelines"] and evaluate(before,row["text"])==evaluate(after,row["text"])==dec["label"]==parent["label"], "INTEGRITY", "Unsafe carry-forward")
        return {"valid":True,"project_id":project,"rule_version":manifest["rule_version"],"record_count":len(rows),"human_history_count":sum(d["origin"]=="human" for d in decisions)}


OPERATIONS = {"health","create","status","list","preview","activate","queue","job_create","claim","submit","step","job_get","cancel","review","history","review_bundle","review_import","export"}
MUTATIONS = {"create","activate","job_create","submit","step","cancel","review","review_import","export"}
