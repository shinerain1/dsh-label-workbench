"""Independently recompute published CFPB metrics; no backend or data download needed."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/cfpb"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    report = json.loads((DOCS / "results.json").read_text(encoding="utf-8"))
    provenance = json.loads((DOCS / "provenance.json").read_text(encoding="utf-8"))
    assert sha(DOCS / "sample-manifest.csv") == report["manifest_sha256"] == provenance["manifest_sha256"]
    assert sha(ROOT / "docs/CFPB-PROTOCOL.md") == report["protocol_sha256"] == provenance["protocol_sha256"]
    assert sha(ROOT / "scripts/cfpb_case.py") == report["script_sha256"]
    assert sha(ROOT / "python/dsh_label_workbench/service.py") == report["service_sha256"]
    manifest = {r["id"]: r for r in csv_read(DOCS / "sample-manifest.csv")}
    assert len(manifest) == 1200
    assert Counter(r["new"] for r in manifest.values()) == report["expected_counts"]
    credit = {"General-purpose credit card or charge card", "Store credit card"}
    prepaid = {"General-purpose prepaid card", "Gift card", "Government benefit card", "Payroll card", "Student prepaid card"}
    for row in manifest.values():
        assert "2023-01-01" <= row["date"] < "2023-08-24"
        assert row["old"] == row["source_product"]
        if row["source_product"] == "Credit card or prepaid card":
            assert row["source_subproduct"] in credit | prepaid
            expected = "Credit card" if row["source_subproduct"] in credit else "Prepaid card"
        else:
            assert row["source_product"] == "Checking or savings account"
            expected = row["source_product"]
        assert row["new"] == expected
    checked = 0
    for run in report["runs"]:
        schedules = []
        for arm in run["arms"]:
            path = DOCS / arm["audit_file"]
            assert sha(path) == arm["audit_sha256"]
            rows = csv_read(path)
            assert len(rows) == len(manifest) and {r["id"] for r in rows} == set(manifest)
            assert all(r["text_sha256"] == manifest[r["id"]]["text_sha256"] and r["expected"] == manifest[r["id"]]["new"] for r in rows)
            human = {r["id"] for r in rows if r["human"] == "1"}
            late = {r["id"] for r in rows if r["late"] == "1"}
            schedules.append((human, late))
            assert len(human) == run["human_records"] and len(late) == run["late_callbacks"] and human <= late
            actual = dict(
                wrong_final_labels=sum(r["label"] != r["expected"] for r in rows),
                stale_current=sum(r["version"] != "2" for r in rows),
                human_overwritten=sum(r["id"] in human and r["origin"] != "human" for r in rows),
                missed_changed_review=sum(manifest[r["id"]]["old"] != r["expected"] and r["selected"] != "1" for r in rows),
                selected_for_review=sum(r["selected"] == "1" for r in rows),
                unchanged_selected=sum(manifest[r["id"]]["old"] == r["expected"] and r["selected"] == "1" for r in rows),
                before_late_wrong_labels=sum(r["before_label"] != r["expected"] for r in rows),
                before_late_stale_current=sum(r["before_version"] != "2" for r in rows),
                before_late_human_overwritten=sum(r["id"] in human and r["before_origin"] != "human" for r in rows),
                final_counts=dict(Counter(r["label"] for r in rows)))
            for metric, value in actual.items():
                assert arm[metric] == value, (run["seed"], arm["arm"], metric, value, arm[metric])
            checked += len(rows)
        assert schedules[0] == schedules[1] == schedules[2], "Unequal events across arms"
    ptc = json.loads((DOCS / "dsh-ptc-results.json").read_text(encoding="utf-8"))
    assert sha(ROOT / "scripts/cfpb-dsh-check.mjs") == ptc["script_sha256"]
    assert ptc["model_calls"] == 0 and ptc["export_audit"]["valid"]
    result = dict(verified=True, audit_rows_recomputed=checked, unique_complaints=len(manifest),
                  runs=len(report["runs"]), arms_per_run=3,
                  scope="Hashes, independent taxonomy mapping, paired schedules and row-level metrics; no claim of verifying consumer allegations or actual human review")
    (DOCS / "evidence-audit.json").write_bytes((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
