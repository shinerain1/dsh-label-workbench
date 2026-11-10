"""Official CFPB taxonomy migration replay; stdlib, real data, controlled faults.

No model and no real human participants. Source classifications are a reference
stream for testing state integrity, not verified ground truth for NLP accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import urllib.request
import zipfile

from dsh_label_workbench.service import Fault, Workbench, verify_export

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".verification/cfpb"
DOCS = ROOT / "docs/cfpb"
URL = "https://files.consumerfinance.gov/f/documents/CCDB_Export_4_November_2022_through_August_2023.zip"
ARCHIVE = LOCAL / "official-2022-11--2023-08.zip"
EXPECTED_ARCHIVE_SHA256 = "547e8bdb07961861d4bacabff74fd2dd7358c04d225b492b286c37920cb39921"
OLD = "Credit card or prepaid card"
CREDIT = "Credit card"
PREPAID = "Prepaid card"
BANK = "Checking or savings account"
TARGETS = (CREDIT, PREPAID, BANK)
SUBPRODUCT = {
    "General-purpose credit card or charge card": CREDIT,
    "Store credit card": CREDIT,
    "General-purpose prepaid card": PREPAID,
    "Gift card": PREPAID,
    "Government benefit card": PREPAID,
    "Payroll card": PREPAID,
    "Student prepaid card": PREPAID,
}
RULE1 = {"mode": "guidelines", "labels": [OLD, BANK], "guidelines":
         "Historical CFPB product classification before 2023-08-24. Credit and prepaid cards share one product. Checking and savings accounts use their own product. Labels are externally supplied from archived consumer selections; this is a taxonomy migration, not validation of complaint allegations."}
RULE2 = {"mode": "guidelines", "labels": list(TARGETS), "guidelines":
         "Normalize the selected historical complaints to the CFPB product taxonomy effective 2023-08-24. Credit and charge/store cards become Credit card. General-purpose prepaid, gift, government benefit, payroll and student prepaid cards become Prepaid card. Checking or savings account stays unchanged. Reference labels come from archived sub-products; do not infer label quality from this replay."}


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    temporary.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def csv_save(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def prepare(download=False):
    LOCAL.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    if download and not ARCHIVE.exists():
        # urllib respects HTTPS_PROXY. Download atomically; existing file is verified below.
        temporary = ARCHIVE.with_suffix(".part")
        with urllib.request.urlopen(URL, timeout=120) as response, temporary.open("wb") as out:
            while block := response.read(1024 * 1024):
                out.write(block)
        temporary.replace(ARCHIVE)
    assert sha(ARCHIVE) == EXPECTED_ARCHIVE_SHA256, "Official archive differs from frozen experiment; do not silently replace the dataset"
    pool = {label: [] for label in TARGETS}
    excluded = Counter()
    source_products = Counter()
    unknown = Counter()
    seen = set()
    total = 0
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        assert len(names) == 1, "Unexpected archive layout"
        with archive.open(names[0]) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                total += 1
                # Official archive uses ISO dates or US slash dates; reject unknown formats.
                date = row["Date received"]
                date = datetime.strptime(date, "%m/%d/%Y" if "/" in date else "%Y-%m-%d").date().isoformat()
                if not "2023-01-01" <= date < "2023-08-24":
                    excluded["outside_date_window"] += 1
                    continue
                product = row["Product"]
                source_products[product] += 1
                if product not in (OLD, BANK):
                    excluded["outside_product_scope"] += 1
                    continue
                sub = row["Sub-product"]
                target = BANK if product == BANK else SUBPRODUCT.get(sub)
                if target is None:
                    unknown[sub] += 1
                    excluded["unmapped_subproduct"] += 1
                    continue
                text = row["Consumer complaint narrative"]
                if not text.strip():
                    excluded["empty_narrative"] += 1
                    continue
                if len(text) > 50000:
                    excluded["text_above_plugin_limit"] += 1
                    continue
                rid = str(row["Complaint ID"])
                assert rid not in seen, "Duplicate complaint ID"
                seen.add(rid)
                pool[target].append(dict(id=rid, date=date, source_product=product,
                                         source_subproduct=sub, old=product, new=target,
                                         text_sha256=text_sha(text), text=text))
    rows = []
    for label in TARGETS:
        assert len(pool[label]) >= 400, f"Insufficient eligible records: {label} {len(pool[label])}"
        rows += sorted(pool[label], key=lambda r: text_sha("20230824:" + r["id"]))[:400]
    rows.sort(key=lambda r: r["id"])
    save(LOCAL / "sample.json", rows)
    manifest = [{k: v for k, v in row.items() if k != "text"} for row in rows]
    csv_save(DOCS / "sample-manifest.csv", manifest)
    save(LOCAL / "ptc-input.json", dict(records=[dict(id=r["id"], text=r["text"]) for r in rows[:90]],
                                       old={r["id"]: r["old"] for r in rows[:90]},
                                       new={r["id"]: r["new"] for r in rows[:90]}, rule1=RULE1, rule2=RULE2))
    provenance = dict(kind="official_public_complaints_historical_taxonomy_replay",
                      retrieved_utc=datetime.now(timezone.utc).isoformat(), url=URL,
                      archive_bytes=ARCHIVE.stat().st_size, archive_sha256=sha(ARCHIVE),
                      archive_csv=names[0], scanned_rows=total,
                      date_window=["2023-01-01", "2023-08-24 (exclusive)"],
                      date_window_source_products=dict(source_products), exclusions=dict(excluded),
                      unmapped_subproducts=dict(unknown), eligible_per_target={k: len(v) for k, v in pool.items()},
                      sample_per_target=dict(Counter(r["new"] for r in rows)),
                      manifest_sha256=sha(DOCS / "sample-manifest.csv"),
                      sample_json_sha256=sha(LOCAL / "sample.json"),
                      protocol_sha256=sha(ROOT / "docs/CFPB-PROTOCOL.md"),
                      unique_narratives=len({r["text_sha256"] for r in rows}),
                      label_source="Consumer-selected subproduct mapped to official 2023 taxonomy; not expert NLP gold")
    save(DOCS / "provenance.json", provenance)
    print(json.dumps(provenance, ensure_ascii=False), flush=True)


def wait_file(path, seconds=120):
    deadline = time.monotonic() + seconds
    while not Path(path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(0.05)


def worker(folder):
    """Separate OS process claims old work, waits for v2, then commits twice."""
    request = read(folder / "worker-request.json")
    guarded = request["arm"] == "guarded_json"
    if request["arm"] == "workbench":
        w = Workbench(folder / "store")
        lease = w.call("claim", dict(job_id=request["job"], limit=500, lease_seconds=3600))
        payload = [dict(record_id=x["record_id"], token=x["token"], label=request["labels"][x["record_id"]]) for x in lease["items"]]
        assert len(payload) == len(request["labels"])
    save(folder / "claimed.json", {"pid": os.getpid(), "count": len(request["labels"])})
    wait_file(folder / "release.json")
    if request["arm"] == "workbench":
        first = w.call("submit", dict(job_id=request["job"], results=payload))
        with w.connect() as con:
            before = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        repeated = w.call("submit", dict(job_id=request["job"], results=payload))
        with w.connect() as con:
            after = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        result = dict(isolated=sum(x["disposition"] == "quarantined" for x in first["outcomes"]),
                      replayed=sum(bool(x.get("replayed")) for x in repeated["outcomes"]),
                      replay_new_decisions=after-before,
                      reasons=dict(Counter(x["reason"] for x in first["outcomes"])))
    else:
        state = read(folder / "state.json")
        isolated = 0
        for replay in range(2):
            for rid, label in request["labels"].items():
                if guarded and (state["version"] != 1 or state["current"][rid]["origin"] == "human"):
                    isolated += int(replay == 0)
                else:
                    state["current"][rid] = dict(label=label, version=1, origin="machine", pending=False)
            save(folder / "state.json", state)
            state = read(folder / "state.json")
        result = dict(isolated=isolated, replayed=None, replay_new_decisions=None, reasons={})
    save(folder / "worker-result.json", result)


def events(rows, seed):
    rng = random.Random(seed)
    human, late = [], []
    for label in TARGETS:
        ids = sorted(r["id"] for r in rows if r["new"] == label)
        rng.shuffle(ids)
        human += ids[:8]
        late += ids[:40]
    return set(human), set(late)


def review(w, pid, rid, label, version):
    with w.connect() as con:
        revision = w.current(con, pid, rid)["revision"]
    w.call("review", dict(project_id=pid, record_id=rid, expected_version=version,
                          expected_revision=revision, label=label, reviewer="simulated-cfpb-migration-reviewer",
                          note="Controlled reference-confirmation event, no real human participant"), human=True)


def fill(w, job, labels):
    count = 0
    while True:
        lease = w.call("claim", dict(job_id=job, limit=100, lease_seconds=3600))
        if not lease["items"]:
            break
        reply = w.call("submit", dict(job_id=job, results=[dict(record_id=x["record_id"], token=x["token"], label=labels[x["record_id"]]) for x in lease["items"]]))
        assert all(x["disposition"] == "applied" for x in reply["outcomes"])
        count += len(lease["items"])
        if reply["state"] == "completed":
            break
    return count


def json_export(state, destination, guarded):
    if guarded and any(x["pending"] or x["version"] != state["version"] for x in state["current"].values()):
        return "REVIEW_INCOMPLETE"
    save(destination, state["current"])
    return "UNSAFE_ACCEPTED" if any(x["pending"] or x["version"] != state["version"] for x in state["current"].values()) else "ACCEPTED"


def run_arm(rows, human, late, seed, arm, run_root):
    folder = run_root / str(seed) / arm
    folder.mkdir(parents=True, exist_ok=False)
    by_id = {r["id"]: r for r in rows}
    old = {r["id"]: r["old"] for r in rows}
    new = {r["id"]: r["new"] for r in rows}
    request = dict(arm=arm, labels={rid: old[rid] for rid in sorted(late)})
    plugin = arm == "workbench"
    if plugin:
        w = Workbench(folder / "store")
        pid = w.call("create", dict(name="CFPB real taxonomy migration", records=[dict(id=r["id"], text=r["text"]) for r in rows], rule=RULE1), "create", session_id="cfpb-session")["project_id"]
        request["job"] = w.call("job_create", dict(project_id=pid, expected_version=1, record_ids=sorted(late)))["job_id"]
    save(folder / "worker-request.json", request)
    log = (folder / "worker.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--folder", str(folder)], stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_file(folder / "claimed.json")
        assert read(folder / "claimed.json")["pid"] != os.getpid()
        if plugin:
            initial = w.call("job_create", dict(project_id=pid, expected_version=1))["job_id"]
            assert fill(w, initial, old) == len(rows)
            for rid in sorted(human):
                review(w, pid, rid, old[rid], 1)
            preview = w.call("preview", dict(project_id=pid, rule=RULE2))
            selected = {x["record_id"] for x in preview["items"] if not x["carry"]}
            w.call("activate", dict(project_id=pid, expected_version=1, rule=RULE2))
            def export_attempt():
                try:
                    w.call("export", dict(project_id=pid, expected_version=2))
                    return "UNSAFE_ACCEPTED"
                except Fault as exc:
                    assert exc.code == "REVIEW_INCOMPLETE", exc.code
                    return exc.code
            early = export_attempt()
            job = w.call("job_create", dict(project_id=pid, expected_version=2, record_ids=sorted(selected-human)))["job_id"]
            machine_count = fill(w, job, new)
            before_human = export_attempt()
            for rid in sorted(human):
                review(w, pid, rid, new[rid], 2)
            # New service object reads only durable state, not prior Python objects.
            w = Workbench(folder / "store")
            recovered = w.call("status", dict(project_id=pid))["pending"] == 0
            with w.connect() as con:
                before_callback = {rid: w.decision(con, w.current(con, pid, rid)["decision"]) for rid in by_id}
        else:
            guarded = arm == "guarded_json"
            state = dict(version=1, current={rid: dict(label=label, version=1, origin="machine", pending=False) for rid, label in old.items()}, history=[])
            for rid in sorted(human):
                state["current"][rid]["origin"] = "human"
                state["history"].append(dict(id=rid, version=1, label=old[rid]))
            selected = set(by_id) if guarded else {rid for rid in by_id if old[rid] not in RULE2["labels"]} | human
            state["version"] = 2
            for rid, value in state["current"].items():
                value["pending"] = rid in selected
                if rid not in selected:
                    value["version"] = 2
            save(folder / "state.json", state)
            early = json_export(state, folder / "early-export.json", guarded)
            machine_count = 0
            for index, rid in enumerate(sorted(selected-human)):
                state["current"][rid] = dict(label=new[rid], version=2, origin="machine", pending=False)
                machine_count += 1
                if index % 100 == 99:
                    save(folder / "state.json", state)
            # Baseline exports really exist, including the unresolved human v1 labels.
            before_human = json_export(state, folder / "pre-human-export.json", guarded)
            for rid in sorted(human):
                state["current"][rid] = dict(label=new[rid], version=2, origin="human", pending=False)
                state["history"].append(dict(id=rid, version=2, label=new[rid]))
            save(folder / "state.json", state)
            state = read(folder / "state.json")
            recovered = state["version"] == 2 and not any(x["pending"] for x in state["current"].values())
            before_callback = state["current"]
        save(folder / "release.json", {"active_rule_version": 2})
        assert process.wait(timeout=120) == 0, (folder / "worker.log").read_text(encoding="utf-8")
        callback = read(folder / "worker-result.json")
        if plugin:
            export = w.call("export", dict(project_id=pid, expected_version=2))
            # Audit in a fresh Python process, not via assertions inside the parent service.
            verified = subprocess.run([sys.executable, "-m", "dsh_label_workbench", "verify", export["path"]], capture_output=True, text=True, check=True)
            save(folder / "verify-cli.json", json.loads(verified.stdout))
            audit = verify_export(export["path"])
            with w.connect() as con:
                current = {rid: w.decision(con, w.current(con, pid, rid)["decision"]) for rid in by_id}
            history_count = audit["human_history_count"]
            artifact_valid = audit["valid"]
        else:
            state = read(folder / "state.json")
            current = state["current"]
            history_count = len(state["history"])
            assert json_export(state, folder / "final-export.json", guarded) != "REVIEW_INCOMPLETE"
            artifact_valid = None  # no provenance ZIP verifier in these JSON scripts
        audit_rows = [dict(id=r["id"], text_sha256=r["text_sha256"], expected=r["new"],
                           selected=int(r["id"] in selected), human=int(r["id"] in human), late=int(r["id"] in late),
                           before_label=before_callback[r["id"]]["label"], before_version=before_callback[r["id"]]["version"],
                           before_origin=before_callback[r["id"]]["origin"],
                           label=current[r["id"]]["label"], version=current[r["id"]]["version"],
                           origin=current[r["id"]]["origin"]) for r in rows]
        audit_path = DOCS / f"audit-{seed}-{arm}.csv"
        csv_save(audit_path, audit_rows)
        summary = dict(arm=arm, records=len(rows), wrong_final_labels=sum(x["label"] != x["expected"] for x in audit_rows),
                       before_late_wrong_labels=sum(x["before_label"] != x["expected"] for x in audit_rows),
                       before_late_stale_current=sum(x["before_version"] != 2 for x in audit_rows),
                       before_late_human_overwritten=sum(x["human"] and x["before_origin"] != "human" for x in audit_rows),
                       stale_current=sum(x["version"] != 2 for x in audit_rows),
                       human_overwritten=sum(x["human"] and x["origin"] != "human" for x in audit_rows),
                       missed_changed_review=sum(r["old"] != r["new"] and r["id"] not in selected for r in rows),
                       selected_for_review=len(selected), unchanged_selected=sum(r["old"] == r["new"] and r["id"] in selected for r in rows),
                       new_machine_results=machine_count, human_confirmations=len(human),
                       early_export=early, export_before_human_reconfirmation=before_human,
                       late_callback=callback, retained_human_history=history_count,
                       recovered_from_disk=recovered, artifact_verified=artifact_valid,
                       final_counts=dict(Counter(x["label"] for x in audit_rows)),
                       audit_file=audit_path.name, audit_sha256=sha(audit_path))
        return summary
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        log.close()


def run():
    rows = read(LOCAL / "sample.json")
    provenance = read(DOCS / "provenance.json")
    assert sha(LOCAL / "sample.json") == provenance["sample_json_sha256"]
    assert sha(DOCS / "sample-manifest.csv") == provenance["manifest_sha256"]
    assert sha(ROOT / "docs/CFPB-PROTOCOL.md") == provenance["protocol_sha256"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_root = LOCAL / "runs" / stamp
    report = dict(kind="real_public_data_controlled_fault_replay", model_calls=0, human_events="simulated",
                  source_commit=subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  protocol_sha256=provenance["protocol_sha256"], script_sha256=sha(__file__),
                  service_sha256=sha(ROOT / "python/dsh_label_workbench/service.py"),
                  manifest_sha256=provenance["manifest_sha256"], expected_counts=dict(Counter(r["new"] for r in rows)),
                  changed_records=sum(r["old"] != r["new"] for r in rows), runs=[])
    save(run_root / "frozen-inputs.json", report)
    for seed in (7, 11, 19):
        human, late = events(rows, seed)
        result = dict(seed=seed, human_records=len(human), late_callbacks=len(late), arms=[])
        for arm in ("simple_json", "guarded_json", "workbench"):
            summary = run_arm(rows, human, late, seed, arm, run_root)
            result["arms"].append(summary)
            print(json.dumps({"seed": seed, **summary}, ensure_ascii=False), flush=True)
        report["runs"].append(result)
        save(run_root / "results.json", report)
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    save(DOCS / "results.json", report)
    save(LOCAL / "last-run.json", {"path": str(run_root)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "worker"])
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--folder", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.download)
    elif args.command == "run":
        run()
    else:
        worker(args.folder)


if __name__ == "__main__":
    main()
