import argparse
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


def score_regression(fixture, result):
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
    if fixture.get("split") != "development_reviewed_failure":
        raise ValueError("Only development reviewed-failure fixtures can be replayed")
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
