import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from .core import Assistant, Store


class RecordedCalls:
    name = "reviewed-regression-fixture"

    def __init__(self, calls):
        self.calls = iter(calls)

    def next(self, prompt, traces):
        return next(self.calls, {"tool":"finish","arguments":{"answer":"Recorded regression calls complete."}}), {}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_fixture(fixture):
    if fixture.get("split") != "development_reviewed_failure":
        raise ValueError("Only development reviewed-failure fixtures are supported")
    source=fixture.get("source",{})
    snapshot=fixture.get("source_snapshot",{})
    run=snapshot.get("run")
    feedback=snapshot.get("feedback")
    if not isinstance(run,dict) or not isinstance(feedback,dict):
        raise ValueError("Fixture is missing immutable source snapshots")
    if hashlib.sha256(_canonical(run).encode()).hexdigest()!=source.get("run_sha256"):
        raise ValueError("Run provenance hash mismatch")
    if hashlib.sha256(_canonical(feedback).encode()).hexdigest()!=source.get("feedback_sha256"):
        raise ValueError("Feedback provenance hash mismatch")
    if source.get("run_id")!=run.get("run_id") or source.get("feedback_id")!=feedback.get("id"):
        raise ValueError("Fixture provenance identity mismatch")
    if feedback.get("run_id")!=run.get("run_id") or feedback.get("verdict")!="incorrect":
        raise ValueError("Fixture is not linked to an incorrect review of this run")
    if fixture.get("expected")!=feedback.get("expected"):
        raise ValueError("Expected behavior differs from immutable feedback")
    expected_replay={
        "owner":feedback.get("owner"),"prompt":run.get("prompt", ""),
        "calls":[trace["call"] for trace in run.get("traces",[]) if "call" in trace],
        "unavailable":sorted({trace["result"]["tool"] for trace in run.get("traces",[]) if trace.get("result",{}).get("status")=="unavailable"}),
    }
    if fixture.get("replay")!=expected_replay:
        raise ValueError("Replay inputs differ from immutable run evidence")


def score_regression(fixture, result):
    validate_fixture(fixture)
    expected = fixture["expected"]
    first = next((trace for trace in result["traces"] if "result" in trace), {})
    observed = first.get("result", {})
    checks = {
        "tool": observed.get("tool") == expected.get("tool"),
        "status": observed.get("status") == expected.get("status"),
        "stop_reason": result.get("stop_reason") == expected.get("stop_reason"),
    }
    if "arguments" in expected:
        checks["arguments"] = first.get("call", {}).get("arguments") == expected["arguments"]
    return {"passed": all(checks.values()), "checks": checks}


def replay_fixture(fixture, database):
    validate_fixture(fixture)
    replay = fixture["replay"]
    if not replay.get("calls"):
        raise ValueError("Fixture has no recorded tool calls")
    result = Assistant(
        Store(database), RecordedCalls(replay["calls"]), unavailable=replay.get("unavailable", [])
    ).run(replay["owner"], replay["prompt"])
    return {"fixture_id": fixture["fixture_id"], **score_regression(fixture, result), "result": result}


def main():
    parser = argparse.ArgumentParser(description="Replay exported development regression fixtures")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    with tempfile.TemporaryDirectory() as directory:
        receipt = replay_fixture(fixture, Path(directory) / "regression.sqlite")
    print(json.dumps(receipt, indent=2))
    raise SystemExit(0 if receipt["passed"] else 1)


if __name__ == "__main__":
    main()
