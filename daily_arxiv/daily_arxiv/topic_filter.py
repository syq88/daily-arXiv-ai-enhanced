"""Conservative, auditable application-domain filtering before paid AI summaries."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


DEFAULT_RULES = Path(__file__).resolve().parents[1] / "paper_filter_rules.json"


def enabled_from_environment():
    value = os.environ.get("PAPER_FILTER_ENABLED", "true").strip().lower()
    if value in {"true", "1", "yes", "on", ""}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError("PAPER_FILTER_ENABLED must be true or false")


class ApplicationFilter:
    def __init__(self, rules_path=DEFAULT_RULES):
        raw = Path(rules_path).read_bytes()
        rules = json.loads(raw)
        self.digest = hashlib.sha256(raw).hexdigest()
        self.rules = [
            (rule["id"], rule["label"], [re.compile(p, re.I) for p in rule["patterns"]])
            for rule in rules["rules"]
        ]
        self.spatial = [re.compile(p, re.I) for p in rules["spatial_title_patterns"]]
        self.general = [re.compile(p, re.I) for p in rules.get("general_method_title_patterns", [])]
        self.examples = re.compile(rules["example_context_pattern"], re.I)

    def classify(self, paper):
        title = str(paper.get("title") or "")
        summary = str(paper.get("summary") or "")
        # An explicit application in the title wins even if it says 'world model'
        # or '3D'. A patient world model is not a general embodied world model.
        for reason, label, patterns in self.rules:
            for pattern in patterns:
                match = pattern.search(title)
                if match:
                    return {"exclude": True, "reason": reason, "label": label,
                            "field": "title", "matched_text": match.group()}

        # Keep core spatial methods when a domain appears only as an example or
        # evaluation dataset. Do not ban generic words such as physics or space.
        if any(pattern.search(title) for pattern in self.spatial + self.general):
            return {"exclude": False, "reason": "general_or_spatial_method"}
        intro = summary[:450]
        for reason, label, patterns in self.rules:
            for pattern in patterns:
                match = pattern.search(intro)
                if match and not self.examples.search(intro[:match.start()]):
                    return {"exclude": True, "reason": reason, "label": label,
                            "field": "abstract_intro", "matched_text": match.group()}
        return {"exclude": False, "reason": "no_clear_application_signal"}

    def filter(self, papers, enabled=True):
        kept, excluded = [], []
        for paper in papers:
            decision = self.classify(paper) if enabled else {"exclude": False, "reason": "disabled"}
            if decision["exclude"]:
                excluded.append({"id": paper.get("id"), "title": paper.get("title"),
                                 **decision, "paper": paper})
            else:
                kept.append(paper)
        return kept, excluded


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def filter_file(data_path, report_dir, rules_path=DEFAULT_RULES, enabled=True):
    data_path = Path(data_path)
    source = data_path.read_bytes()
    papers = [json.loads(line) for line in source.decode("utf-8").splitlines() if line.strip()]
    if any(not isinstance(p, dict) or not p.get("id") for p in papers):
        raise ValueError("Invalid paper record; input has not been changed")
    engine = ApplicationFilter(rules_path)
    kept, excluded = engine.filter(papers, enabled)
    source_digest = hashlib.sha256(source).hexdigest()
    report_path = Path(report_dir) / f"{data_path.stem}_{source_digest[:12]}_filter_report.json"
    report = {"version": 1, "enabled": enabled, "rules_sha256": engine.digest,
              "input_sha256": source_digest, "input_count": len(papers),
              "kept_count": len(kept), "excluded_count": len(excluded),
              "counts_by_reason": dict(Counter(p["reason"] for p in excluded)),
              "kept_ids": [p["id"] for p in kept], "excluded": excluded}
    # Archive full excluded records BEFORE changing the input, so decisions can
    # be reviewed/reversed and a failed report write cannot discard any paper.
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write(data_path, "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in kept))
    for item in excluded:
        print(f"FILTER {item['id']} [{item['reason']}/{item['field']}]: {item['title']}")
    print(f"Application filter: {len(papers)} input; {len(kept)} kept; {len(excluded)} excluded.")
    print(f"Filter audit: {report_path}")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--rules", default=str(DEFAULT_RULES))
    args = parser.parse_args()
    filter_file(args.data, args.report_dir, args.rules, enabled_from_environment())


if __name__ == "__main__":
    main()
