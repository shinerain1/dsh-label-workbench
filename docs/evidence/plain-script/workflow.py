#!/usr/bin/env python3
"""Local, dependency-free v1 -> v2 labeling migration and callback quarantine.

Safety properties
-----------------
* records.json, rules-v1.json, rules-v2.json and late-results.json are read-only.
* Machine records are labeled by the executable ordered rules in rules-v2.json.
* Historical human decisions are never overwritten or "approved" by this script:
  they keep their old label/rule_version/history and are marked needs_review.
* A callback is never allowed to mutate state-v2.  It is copied into a quarantine
  ledger keyed by a content fingerprint, so replaying the same callback is a
  no-op (and cannot inflate the isolated count).
* Writes are atomic (temp file + os.replace).  On a later run, a valid persisted
  state-v2.json is recovered and validated rather than rebuilt or re-labeled.
* No network, no external packages, no labeling-workbench library.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
RECORDS_PATH = BASE_DIR / "records.json"
RULES_V1_PATH = BASE_DIR / "rules-v1.json"
RULES_V2_PATH = BASE_DIR / "rules-v2.json"
STATE_V1_PATH = BASE_DIR / "state-v1.json"
STATE_V2_PATH = BASE_DIR / "state-v2.json"
LATE_RESULTS_PATH = BASE_DIR / "late-results.json"
RESULT_PATH = BASE_DIR / "result.json"
RESUME_PATH = BASE_DIR / "resume.json"
QUARANTINE_PATH = BASE_DIR / "isolated-callbacks.json"
LEDGER_PATH = BASE_DIR / "callback-ledger.json"
BACKUP_DIR = BASE_DIR / "backups"
WORKFLOW_PATH = Path(__file__).resolve()

ACTIVE_VERSION = 2
EXPECTED_RECORD_COUNT = 24
REQUIRED_FIELDS = (
    "id",
    "text",
    "label",
    "rule_version",
    "needs_review",
    "source",
    "history",
)


class WorkflowError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise WorkflowError(f"missing required file: {path.name}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"invalid JSON in {path.name}: {exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def backup_file(path: Path) -> Path | None:
    if not path.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = BACKUP_DIR / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, target)
    return target


def validate_rules(document: Any, version: int) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise WorkflowError(f"rules-v{version}.json must be an object")
    if document.get("mode") != "decision_list":
        raise WorkflowError(f"rules-v{version}.json has unsupported mode")
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise WorkflowError(f"rules-v{version}.json has no rules")
    labels = document.get("labels")
    if not isinstance(labels, list) or not labels:
        raise WorkflowError(f"rules-v{version}.json has no labels")
    default = document.get("default")
    if default not in labels:
        raise WorkflowError(f"rules-v{version}.json default is not a label")
    for rule in rules:
        if not isinstance(rule, dict):
            raise WorkflowError(f"rules-v{version}.json rule is not an object")
        if rule.get("label") not in labels:
            raise WorkflowError(f"rules-v{version}.json rule has unknown label")
        keywords = rule.get("contains_any")
        if not isinstance(keywords, list) or not keywords:
            raise WorkflowError(f"rules-v{version}.json rule has no keywords")
        if not all(isinstance(item, str) and item for item in keywords):
            raise WorkflowError(f"rules-v{version}.json has invalid keyword")
    return document


def label_for(text: str, rules_document: dict[str, Any]) -> str:
    folded = text.casefold()
    for rule in rules_document["rules"]:
        for keyword in rule["contains_any"]:
            if keyword.casefold() in folded:
                return rule["label"]
    return rules_document["default"]


def index_by_id(rows: Any, name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise WorkflowError(f"{name} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise WorkflowError(f"{name} contains an invalid row")
        if row["id"] in result:
            raise WorkflowError(f"{name} contains duplicate id {row['id']}")
        result[row["id"]] = row
    return result


def load_inputs() -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    records = read_json(RECORDS_PATH)
    if not isinstance(records, list) or len(records) != EXPECTED_RECORD_COUNT:
        raise WorkflowError(
            f"records.json must contain {EXPECTED_RECORD_COUNT} records"
        )
    if not all(
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("text"), str)
        for row in records
    ):
        raise WorkflowError("records.json contains an invalid record")
    record_ids = [row["id"] for row in records]
    if len(set(record_ids)) != len(record_ids):
        raise WorkflowError("records.json contains duplicate ids")

    v1_rows = read_json(STATE_V1_PATH)
    v1_by_id = index_by_id(v1_rows, "state-v1.json")
    if set(v1_by_id) != set(record_ids):
        raise WorkflowError("state-v1.json ids do not match records.json")

    validate_rules(read_json(RULES_V1_PATH), 1)
    rules_v2 = validate_rules(read_json(RULES_V2_PATH), 2)
    return records, v1_by_id, rules_v2


def normalize_history(value: Any) -> list[Any]:
    return copy.deepcopy(value) if isinstance(value, list) else []


def human_ids_from_v1(v1_by_id: dict[str, dict[str, Any]]) -> set[str]:
    return {
        record_id
        for record_id, row in v1_by_id.items()
        if row.get("source") == "human"
    }


def build_state_v2(
    records: list[dict[str, Any]],
    v1_by_id: dict[str, dict[str, Any]],
    rules_v2: dict[str, Any],
) -> list[dict[str, Any]]:
    humans = human_ids_from_v1(v1_by_id)
    state: list[dict[str, Any]] = []
    for record in records:
        old = v1_by_id[record["id"]]
        if record["id"] in humans:
            # Preserve the historical decision verbatim, but never present it as
            # a v2 confirmation.  Only a human may clear needs_review.
            state.append(
                {
                    "id": record["id"],
                    "text": record["text"],
                    "label": old["label"],
                    "rule_version": old["rule_version"],
                    "needs_review": True,
                    "source": "human",
                    "history": normalize_history(old.get("history")),
                }
            )
        else:
            state.append(
                {
                    "id": record["id"],
                    "text": record["text"],
                    "label": label_for(record["text"], rules_v2),
                    "rule_version": ACTIVE_VERSION,
                    "needs_review": False,
                    "source": "machine",
                    "history": [],
                }
            )
    return state


def validate_state_v2(
    state: Any,
    records: list[dict[str, Any]],
    v1_by_id: dict[str, dict[str, Any]],
    rules_v2: dict[str, Any],
) -> bool:
    """Return True only for a complete, safe, already-migrated handoff state."""
    try:
        if not isinstance(state, list) or len(state) != len(records):
            return False
        humans = human_ids_from_v1(v1_by_id)
        for record, entry in zip(records, state):
            if not isinstance(entry, dict):
                return False
            if any(field not in entry for field in REQUIRED_FIELDS):
                return False
            if entry["id"] != record["id"] or entry["text"] != record["text"]:
                return False
            if not isinstance(entry["history"], list):
                return False
            if entry["id"] in humans:
                old = v1_by_id[entry["id"]]
                if old.get("source") != "human":
                    return False
                if entry["source"] != "human" or entry["needs_review"] is not True:
                    return False
                if entry["label"] != old["label"]:
                    return False
                if entry["rule_version"] != old["rule_version"]:
                    return False
                if entry["history"] != normalize_history(old.get("history")):
                    return False
            else:
                if v1_by_id[entry["id"]].get("source") == "human":
                    return False
                expected = label_for(record["text"], rules_v2)
                if entry["source"] != "machine" or entry["needs_review"] is not False:
                    return False
                if entry["label"] != expected:
                    return False
                if entry["rule_version"] != ACTIVE_VERSION:
                    return False
                if entry["history"] != []:
                    return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def callback_fingerprint(callback: dict[str, Any]) -> str:
    canonical = json.dumps(
        callback, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty_ledger() -> dict[str, Any]:
    return {"active_version": ACTIVE_VERSION, "callbacks": []}


def load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.is_file():
        return empty_ledger()
    try:
        ledger = read_json(LEDGER_PATH)
    except WorkflowError:
        backup_file(LEDGER_PATH)
        return empty_ledger()
    if (
        not isinstance(ledger, dict)
        or not isinstance(ledger.get("callbacks"), list)
        or not all(isinstance(row, dict) for row in ledger["callbacks"])
    ):
        backup_file(LEDGER_PATH)
        return empty_ledger()
    ledger["active_version"] = ACTIVE_VERSION
    return ledger


def load_quarantine() -> dict[str, Any]:
    if not QUARANTINE_PATH.is_file():
        return {
            "active_version": ACTIVE_VERSION,
            "policy": (
                "Callbacks are audit data only and never mutate state-v2. "
                "Historical human records are always protected."
            ),
            "entries": [],
        }
    try:
        quarantine = read_json(QUARANTINE_PATH)
    except WorkflowError:
        backup_file(QUARANTINE_PATH)
        quarantine = None
    if (
        not isinstance(quarantine, dict)
        or not isinstance(quarantine.get("entries"), list)
    ):
        backup_file(QUARANTINE_PATH) if QUARANTINE_PATH.is_file() else None
        quarantine = {
            "active_version": ACTIVE_VERSION,
            "policy": (
                "Callbacks are audit data only and never mutate state-v2. "
                "Historical human records are always protected."
            ),
            "entries": [],
        }
    quarantine["active_version"] = ACTIVE_VERSION
    return quarantine


def process_callback(
    callback: Any,
    known_ids: set[str],
    valid_labels: set[str],
    ledger: dict[str, Any],
    quarantine: dict[str, Any],
) -> dict[str, Any]:
    """Quarantine a callback exactly once.  Never mutate state-v2."""
    if not isinstance(callback, dict):
        raise WorkflowError("late-results.json must be an object")
    results = callback.get("results")
    if not isinstance(results, list):
        raise WorkflowError("late-results.json.results must be an array")

    fingerprint = callback_fingerprint(callback)
    existing = next(
        (
            row
            for row in ledger["callbacks"]
            if row.get("fingerprint") == fingerprint
        ),
        None,
    )
    quarantined = next(
        (
            row
            for row in quarantine["entries"]
            if row.get("fingerprint") == fingerprint
        ),
        None,
    )

    if existing is not None:
        # Replay of an already-processed callback: no state change, no new
        # ledger entry, and no increase in the isolated count.
        if quarantined is None:
            # Repair a missing quarantine copy without double-counting.
            quarantine["entries"].append(
                {
                    **existing,
                    "results": copy.deepcopy(results),
                    "state_mutated": False,
                    "recovered_quarantine_copy": True,
                }
            )
        return {
            "duplicate": True,
            "fingerprint": fingerprint,
            "rule_version": existing.get("rule_version"),
            "result_count": int(existing.get("result_count", 0)),
            "reason": existing.get("reason", "already_processed"),
            "ledger_changed": False,
            "quarantine_changed": quarantined is None,
        }

    clean_results: list[dict[str, str]] = []
    malformed = 0
    for row in results:
        if (
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("label"), str)
            and row["id"] in known_ids
            and row["label"] in valid_labels
        ):
            clean_results.append({"id": row["id"], "label": row["label"]})
        else:
            malformed += 1

    callback_version = callback.get("rule_version")
    if callback_version != ACTIVE_VERSION:
        reason = "stale_rule_version"
    else:
        reason = "callback_quarantined_for_reconciliation"

    entry = {
        "fingerprint": fingerprint,
        "rule_version": callback_version,
        "result_count": len(clean_results),
        "malformed_count": malformed,
        "ids": [row["id"] for row in clean_results],
        "reason": reason,
        "processed_at": now_utc(),
        "state_mutated": False,
    }
    ledger["callbacks"].append(entry)
    quarantine["entries"].append({**entry, "results": copy.deepcopy(clean_results)})
    return {
        "duplicate": False,
        "fingerprint": fingerprint,
        "rule_version": callback_version,
        "result_count": len(clean_results),
        "reason": reason,
        "ledger_changed": True,
        "quarantine_changed": True,
    }


def build_impact(
    records: list[dict[str, Any]],
    v1_by_id: dict[str, dict[str, Any]],
    rules_v2: dict[str, Any],
) -> dict[str, Any]:
    humans = human_ids_from_v1(v1_by_id)
    relabeled: list[dict[str, str]] = []
    unchanged_machine = 0
    pending_human: list[dict[str, Any]] = []
    for record in records:
        old = v1_by_id[record["id"]]
        v2_label = label_for(record["text"], rules_v2)
        if record["id"] in humans:
            pending_human.append(
                {
                    "id": record["id"],
                    "kept_label": old["label"],
                    "kept_rule_version": old["rule_version"],
                    "v2_rule_label": v2_label,
                    "needs_review": True,
                }
            )
            continue
        if old["label"] != v2_label:
            relabeled.append(
                {
                    "id": record["id"],
                    "old_label": old["label"],
                    "new_label": v2_label,
                }
            )
        else:
            unchanged_machine += 1
    return {
        "machine_total": len(records) - len(humans),
        "machine_relabeled_count": len(relabeled),
        "machine_relabeled": relabeled,
        "machine_unchanged_count": unchanged_machine,
        "human_total": len(humans),
        "human_pending": pending_human,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate local labels from v1 to v2 and quarantine late callbacks."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="require an existing state-v2.json and recover it instead of creating it",
    )
    args = parser.parse_args()

    records, v1_by_id, rules_v2 = load_inputs()
    humans = human_ids_from_v1(v1_by_id)

    existing_state: Any = None
    state_exists = STATE_V2_PATH.is_file()
    if state_exists:
        try:
            existing_state = read_json(STATE_V2_PATH)
        except WorkflowError:
            existing_state = None

    recovered = state_exists and validate_state_v2(
        existing_state, records, v1_by_id, rules_v2
    )

    if args.resume and not state_exists:
        raise WorkflowError(
            "--resume requested but state-v2.json does not exist; refusing to rebuild"
        )

    state_written = False
    if recovered:
        state = existing_state
    else:
        # This is the only path that calculates labels.  It is used for the
        # initial migration, or to recover from a missing/corrupt/unsafe state.
        if state_exists:
            backup_file(STATE_V2_PATH)
        state = build_state_v2(records, v1_by_id, rules_v2)
        atomic_write_json(STATE_V2_PATH, state)
        state_written = True

    # The new state is persisted before any callback is looked at, so a stale
    # callback can never be applied to an in-flight v2 migration.
    callback = read_json(LATE_RESULTS_PATH)
    ledger = load_ledger()
    quarantine = load_quarantine()
    outcome = process_callback(
        callback,
        known_ids={row["id"] for row in records},
        valid_labels=set(rules_v2["labels"]),
        ledger=ledger,
        quarantine=quarantine,
    )
    if outcome["ledger_changed"]:
        atomic_write_json(LEDGER_PATH, ledger)
    if outcome["quarantine_changed"]:
        atomic_write_json(QUARANTINE_PATH, quarantine)

    pending_human_ids = [row["id"] for row in state if row.get("needs_review")]
    all_v2 = all(
        row.get("rule_version") == ACTIVE_VERSION and not row.get("needs_review")
        for row in state
    )
    export_allowed = bool(all_v2 and not pending_human_ids)
    isolated_callback_count = sum(
        int(row.get("result_count", 0)) for row in ledger["callbacks"]
    )
    isolated_batches = len(ledger["callbacks"])
    impact = build_impact(records, v1_by_id, rules_v2)

    result = {
        "active_version": ACTIVE_VERSION,
        "pending_human_ids": pending_human_ids,
        "export_allowed": export_allowed,
        "isolated_callback_count": isolated_callback_count,
        "isolated_callback_batches": isolated_batches,
        "state_recovered": recovered,
        "state_written_this_run": state_written,
        "last_callback": {
            "fingerprint": outcome["fingerprint"],
            "rule_version": outcome["rule_version"],
            "result_count": outcome["result_count"],
            "status": "duplicate_ignored" if outcome["duplicate"] else "isolated",
            "reason": outcome["reason"],
        },
        "impact": impact,
        "artifacts": {
            "workflow": str(WORKFLOW_PATH),
            "state_v2": str(STATE_V2_PATH),
            "result": str(RESULT_PATH),
            "resume": str(RESUME_PATH),
            "isolated_callbacks": str(QUARANTINE_PATH),
            "callback_ledger": str(LEDGER_PATH),
        },
    }

    resume = {
        "recovered": recovered,
        "pending_human_ids": pending_human_ids,
        "export_allowed": export_allowed,
        "active_version": ACTIVE_VERSION,
        "state_unchanged_during_resume": recovered and not state_written,
        "callback_replay_ignored": bool(outcome["duplicate"]),
        "isolated_callback_count": isolated_callback_count,
        "state_path": str(STATE_V2_PATH),
        "result_path": str(RESULT_PATH),
    }

    atomic_write_json(RESULT_PATH, result)
    atomic_write_json(RESUME_PATH, resume)

    print(
        json.dumps(
            {
                "active_version": ACTIVE_VERSION,
                "recovered": recovered,
                "state_written": state_written,
                "pending_human_ids": pending_human_ids,
                "export_allowed": export_allowed,
                "isolated_callback_count": isolated_callback_count,
                "callback_replay_ignored": bool(outcome["duplicate"]),
                "machine_relabeled_count": impact["machine_relabeled_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        print(f"workflow error: {exc}", file=sys.stderr)
        raise SystemExit(2)
