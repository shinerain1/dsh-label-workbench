"""Check actual local delivery ZIPs against public source manifest and audit CSVs."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import zipfile

from dsh_label_workbench.service import verify_export

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/cfpb"


def csv_read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return {r["id"]: r for r in csv.DictReader(stream)}


def main():
    location = json.loads((ROOT / ".verification/cfpb/last-run.json").read_text(encoding="utf-8"))
    source = csv_read(DOCS / "sample-manifest.csv")
    results = []
    for seed in (7, 11, 19):
        files = list((Path(location["path"]) / str(seed) / "workbench/store/exports").glob("*.zip"))
        assert len(files) == 1, "Expected exactly one successful final export per run"
        path = files[0]
        audit = verify_export(path)
        reference = csv_read(DOCS / f"audit-{seed}-workbench.csv")
        with zipfile.ZipFile(path) as archive:
            rows = [json.loads(line) for line in archive.read("data.jsonl").decode("utf-8").splitlines()]
        assert len(rows) == len(source) and {r["id"] for r in rows} == set(source)
        for row in rows:
            rid = row["id"]
            assert hashlib.sha256(row["text"].encode("utf-8")).hexdigest() == source[rid]["text_sha256"]
            assert row["label"] == source[rid]["new"] == reference[rid]["label"]
            assert row["rule_version"] == int(reference[rid]["version"]) == 2
            assert row["origin"] == reference[rid]["origin"]
        assert audit["human_history_count"] == 48
        results.append(dict(seed=seed, zip_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            records_checked=len(rows), text_hashes_match=True, reference_labels_match=True,
                            public_audit_matches_actual_export=True, human_history_count=audit["human_history_count"],
                            counts=dict(Counter(r["label"] for r in rows))))
    report = dict(verified=True, kind="independent_actual_export_to_source_manifest_check", runs=results)
    (DOCS / "export-audit.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
