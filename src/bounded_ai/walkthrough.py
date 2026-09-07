import hashlib
import json
import tempfile
from pathlib import Path

from .core import Assistant, ROOT, Store
from .regression import replay_fixture, score_regression


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
        wrong_args = Assistant(store, Calls([
            {"tool":"propose_import","arguments":{"values":[999]}},
            {"tool":"finish","arguments":{"answer":"done"}},
        ])).run("alpha", "Import 1, 2, 3")
        args_review, args_fixture = reviewed_fixture(store, wrong_args, "The proposed values differ from the requested values.", {
            "tool":"propose_import","status":"ok","arguments":{"values":[1,2,3]},"stop_reason":"finished"
        })
        corrected_args = Assistant(Store(root/"corrected-args.sqlite"), Calls([
            {"tool":"propose_import","arguments":{"values":[1,2,3]}},
            {"tool":"finish","arguments":{"answer":"done"}},
        ])).run("alpha", "Import 1, 2, 3")
        unavailable = Assistant(store, Calls([{"tool":"get_task","arguments":{"task_id":"alpha-task"}}]), unavailable=["get_task"]).run("alpha", "Read alpha-task")
        unavailable_review, unavailable_fixture = reviewed_fixture(store, unavailable, "The owner task should have been available.", {
            "tool":"get_task","status":"ok","arguments":{"task_id":"alpha-task"},"stop_reason":"finished"
        })
        available = Assistant(Store(root/"available.sqlite"), Calls([
            {"tool":"get_task","arguments":{"task_id":"alpha-task"}},
            {"tool":"finish","arguments":{"answer":"done"}},
        ])).run("alpha", "Read alpha-task")
        receipt = {
            "schema_version":1,
            "title":"Synthetic workflow-inspector walkthrough",
            "held_out_eval_sha256":hashlib.sha256((ROOT/"data/eval.json").read_bytes()).hexdigest(),
            "cases":[
                {"name":"changed tool arguments","inspection":store.inspect_run("alpha",wrong_args["run_id"]),"feedback":args_review,"fixture":args_fixture,"replay_before_change":replay_fixture(args_fixture,root/"args-replay.sqlite"),"candidate_after_change":score_regression(args_fixture,corrected_args)},
                {"name":"configured tool unavailable","inspection":store.inspect_run("alpha",unavailable["run_id"]),"feedback":unavailable_review,"fixture":unavailable_fixture,"replay_before_change":replay_fixture(unavailable_fixture,root/"unavailable-replay.sqlite"),"candidate_after_change":score_regression(unavailable_fixture,available)},
            ],
            "limitations":["Exact synthetic local receipts","No model or external API called","No approval, action execution or automatic retry","Development fixtures do not modify held-out evaluation data"],
        }
    destination=ROOT/"docs/inspector-receipts.json"
    destination.write_text(json.dumps(receipt,indent=2)+"\n")
    print(destination)


if __name__ == "__main__":
    main()
