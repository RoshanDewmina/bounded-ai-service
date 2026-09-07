import hashlib
import json
import tempfile
from pathlib import Path

from .core import Assistant, ROOT, Store
from .regression import replay_fixture


class Calls:
    name = "synthetic-walkthrough-fixture"

    def __init__(self, calls):
        self.calls = iter(calls)

    def next(self, prompt, traces):
        return next(self.calls), {}


def reviewed_fixture(store, run, reason, expected):
    review = store.add_feedback("alpha", run["run_id"], "incorrect", reason, expected)
    return review, store.export_regression("alpha", run["run_id"], review["feedback_id"])


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = Store(root / "walkthrough.sqlite")
        denied = Assistant(store, Calls([{"tool":"get_task","arguments":{"task_id":"beta-task"}}])).run("alpha", "Read beta-task")
        denied_review, denied_fixture = reviewed_fixture(store, denied, "Expected denial is retained as a regression guard for cross-owner access.", {
            "tool":"get_task","status":"denied","arguments":{"task_id":"beta-task"},"stop_reason":"denied"
        })
        unavailable = Assistant(store, Calls([{"tool":"get_task","arguments":{"task_id":"alpha-task"}}]), unavailable=["get_task"]).run("alpha", "Read alpha-task")
        unavailable_review, unavailable_fixture = reviewed_fixture(store, unavailable, "Expected unavailable result is retained without retrying the tool.", {
            "tool":"get_task","status":"unavailable","arguments":{"task_id":"alpha-task"},"stop_reason":"unavailable"
        })
        receipt = {
            "schema_version":1,
            "title":"Synthetic workflow-inspector walkthrough",
            "held_out_eval_sha256":hashlib.sha256((ROOT/"data/eval.json").read_bytes()).hexdigest(),
            "cases":[
                {"name":"cross-owner denial","inspection":store.inspect_run("alpha",denied["run_id"]),"feedback":denied_review,"fixture":denied_fixture,"replay":replay_fixture(denied_fixture,root/"denied-replay.sqlite")},
                {"name":"configured tool unavailable","inspection":store.inspect_run("alpha",unavailable["run_id"]),"feedback":unavailable_review,"fixture":unavailable_fixture,"replay":replay_fixture(unavailable_fixture,root/"unavailable-replay.sqlite")},
            ],
            "limitations":["Exact synthetic local receipts","No model or external API called","No approval, action execution or automatic retry","Development fixtures do not modify held-out evaluation data"],
        }
    destination=ROOT/"docs/inspector-receipts.json"
    destination.write_text(json.dumps(receipt,indent=2)+"\n")
    print(destination)


if __name__ == "__main__":
    main()
