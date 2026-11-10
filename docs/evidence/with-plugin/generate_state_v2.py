"""Build the v2 handoff from immutable inputs.

Human-reviewed records are intentionally NOT relabeled: they keep their old
label/version and are flagged for human reconfirmation under v2. Machine
records are evaluated against the complete executable v2 decision list.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
ACTIVE_VERSION = 2
HUMAN_IDS = {"r00005", "r00018"}
ISOLATED_CALLBACK_COUNT = 24


def load(name):
    with (ROOT / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def classify(text, rule):
    folded = text.casefold()
    for item in rule["rules"]:
        if any(keyword.casefold() in folded for keyword in item["contains_any"]):
            return item["label"]
    return rule["default"]


def main():
    records = load("records.json")
    old_state = {item["id"]: item for item in load("state-v1.json")}
    v2_rule = load("rules-v2.json")

    state = []
    changed, unchanged = [], []
    pending_human = []

    for record in records:
        rid = record["id"]
        old = old_state[rid]
        if old["source"] == "human":
            # Never approve or rewrite a human decision during a rule change.
            entry = {
                "id": rid,
                "text": record["text"],
                "label": old["label"],
                "rule_version": old["rule_version"],
                "needs_review": True,
                "source": "human",
                "history": old.get("history", []),
            }
            pending_human.append(rid)
        else:
            new_label = classify(record["text"], v2_rule)
            entry = {
                "id": rid,
                "text": record["text"],
                "label": new_label,
                "rule_version": ACTIVE_VERSION,
                "needs_review": False,
                "source": "machine",
                "history": old.get("history", []),
            }
            if old["label"] != new_label:
                changed.append({
                    "id": rid,
                    "text": record["text"],
                    "from": old["label"],
                    "to": new_label,
                })
            else:
                unchanged.append(rid)

        assert entry["label"] in v2_rule["labels"]
        state.append(entry)

    assert len(state) == 24
    assert len({item["id"] for item in state}) == 24
    assert pending_human == ["r00005", "r00018"]

    with (ROOT / "state-v2.json").open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    impact = {
        "from_version": 1,
        "to_version": ACTIVE_VERSION,
        "total_records": len(state),
        "machine_relabeled_count": len(changed),
        "machine_carried_count": len(unchanged),
        "changed": changed,
        "carried_ids": unchanged,
        "human_held_for_reconfirmation": pending_human,
        "late_v1_callback_isolated_count": ISOLATED_CALLBACK_COUNT,
    }
    with (ROOT / "impact-analysis.json").open("w", encoding="utf-8") as fh:
        json.dump(impact, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    result = {
        "active_version": ACTIVE_VERSION,
        "pending_human_ids": pending_human,
        "export_allowed": False,
        "isolated_callback_count": ISOLATED_CALLBACK_COUNT,
        "script_path": str(ROOT / "generate_state_v2.py"),
        "state_path": str(ROOT / "state-v2.json"),
        "impact_path": str(ROOT / "impact-analysis.json"),
        "artifacts": [
            str(ROOT / "generate_state_v2.py"),
            str(ROOT / "state-v2.json"),
            str(ROOT / "impact-analysis.json"),
        ],
    }
    with (ROOT / "result.json").open("w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps({
        "relabeled": len(changed),
        "carried": len(unchanged),
        "pending_human": pending_human,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
