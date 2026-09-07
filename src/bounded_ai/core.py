import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[2]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal["search_docs", "get_task", "propose_import", "finish"]
    arguments: dict


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=300)


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class ImportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: list[float] = Field(min_length=1, max_length=100)

    @field_validator("values", mode="before")
    @classmethod
    def finite_values(cls, values):
        if not isinstance(values, list) or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in values):
            raise ValueError("Expected finite numbers between -1000000 and 1000000")
        return values


class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(max_length=1000)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: str
    status: Literal["ok", "denied", "invalid", "unavailable"]
    data: dict


class PolicyError(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(proposal_id TEXT PRIMARY KEY,owner TEXT NOT NULL,workflow_id TEXT NOT NULL UNIQUE,approved REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS run_feedback(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL,
                verdict TEXT NOT NULL CHECK(verdict IN ('correct','incorrect')),
                reason TEXT NOT NULL, expected TEXT NOT NULL, created REAL NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS regression_fixtures(
                id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL,
                owner TEXT NOT NULL, fixture TEXT NOT NULL, created REAL NOT NULL,
                FOREIGN KEY(feedback_id) REFERENCES run_feedback(id),
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS run_owner_feedback ON run_feedback(owner,run_id,created);
            CREATE INDEX IF NOT EXISTS run_owner_created ON runs(owner,id);
            ''')
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "created" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN created REAL NOT NULL DEFAULT 0")
            db.execute("INSERT OR IGNORE INTO tasks VALUES ('alpha-task','alpha','succeeded','{\"sum\":6}')")
            db.execute("INSERT OR IGNORE INTO tasks VALUES ('beta-task','beta','failed','null')")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def task(self, owner, task_id):
        with self.connect() as db:
            row = db.execute("SELECT id,state,result FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        if row is None:
            raise PolicyError("Task unavailable to this principal")
        return {"task_id": row["id"], "state": row["state"], "result": json.loads(row["result"])}

    def propose(self, owner, values):
        payload = json.dumps({"kind": "data_import", "records": [{"value": v} for v in values]}, sort_keys=True, separators=(",", ":"))
        pid = str(uuid.uuid4())
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM proposals").fetchone()[0] >= 1000:
                raise PolicyError("Demo proposal capacity reached; operator reset required")
            db.execute("INSERT INTO proposals VALUES (?,?,?,?,?)", (pid, owner, payload, hashlib.sha256(payload.encode()).hexdigest(), time.time()))
        return {"proposal_id": pid, "action": json.loads(payload), "approval_required": True}

    def approve(self, owner, proposal_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM proposals WHERE id=? AND owner=?", (proposal_id, owner)).fetchone()
            if row is None:
                raise PolicyError("Proposal unavailable to this principal")
            previous = db.execute("SELECT workflow_id FROM approvals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if previous:
                return {"workflow_id": previous[0], "replayed": True, "execution": "synthetic_local"}
            if time.time() - row["created"] > 900:
                raise PolicyError("Proposal expired; request a new proposal")
            if hashlib.sha256(row["payload"].encode()).hexdigest() != row["digest"]:
                raise PolicyError("Proposal integrity failure")
            wid = str(uuid.uuid4())
            values = [r["value"] for r in json.loads(row["payload"])["records"]]
            db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (wid, owner, "succeeded", json.dumps({"sum": sum(values), "count": len(values)})))
            db.execute("INSERT INTO approvals VALUES (?,?,?,?)", (proposal_id, owner, wid, time.time()))
        return {"workflow_id": wid, "replayed": False, "execution": "synthetic_local"}

    def save_run(self, owner, response):
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 1000:
                raise PolicyError("Demo trace capacity reached")
            db.execute("INSERT INTO runs(id,owner,response,created) VALUES (?,?,?,?)", (response["run_id"], owner, json.dumps(response), time.time()))

    def get_run(self, owner, run_id):
        with self.connect() as db:
            row = db.execute("SELECT response FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if not row:
            raise PolicyError("Run unavailable to this principal")
        return json.loads(row[0])

    def list_runs(self, owner, limit=20):
        with self.connect() as db:
            rows = db.execute('''
                SELECT r.response,r.created,f.verdict,f.reason
                FROM runs r
                LEFT JOIN run_feedback f ON f.id=(
                    SELECT id FROM run_feedback WHERE run_id=r.id AND owner=r.owner
                    ORDER BY created DESC,id DESC LIMIT 1
                )
                WHERE r.owner=? ORDER BY r.created DESC,r.id DESC LIMIT ?
            ''', (owner, limit)).fetchall()
        summaries=[]
        for row in rows:
            run=json.loads(row["response"])
            summaries.append({
                "run_id":run["run_id"], "provider":run["provider"],
                "stop_reason":run["stop_reason"], "latency_ms":run["latency_ms"],
                "step_count":len(run["traces"]),
                "created_at":run.get("created_at") or datetime.fromtimestamp(row["created"],timezone.utc).isoformat(),
                "feedback":None if row["verdict"] is None else {"verdict":row["verdict"],"reason":row["reason"]},
            })
        return summaries

    @staticmethod
    def _step_inspection(trace):
        result=trace.get("result")
        if result is None:
            return {
                "step":trace.get("step"), "call":None,
                "policy_expectation":"Provider returns a typed tool call before the deadline.",
                "observed":{"provider_error":trace.get("error","unknown")},
                "outcome":{"code":"provider_error_observed","reason":"The recorded provider exception type is the only available cause; no deeper cause is inferred."},
                "duration_ms":trace.get("duration_ms"),
            }
        status=result["status"]
        reasons={
            "ok":("tool_result_verified","The server validated the call and recorded a typed tool result."),
            "denied":("owner_policy_denied",result.get("data",{}).get("error","The owner-scoped policy denied the call.")),
            "invalid":("tool_schema_rejected",result.get("data",{}).get("error","The server rejected the tool schema.")),
            "unavailable":("tool_unavailable",result.get("data",{}).get("error","The configured tool was unavailable.")),
        }
        code,reason=reasons[status]
        return {
            "step":trace.get("step"), "call":trace.get("call"),
            "policy_expectation":"A schema-valid, owner-authorized, available tool call returns status ok; every other outcome is explicit.",
            "observed":{"tool":result.get("tool"),"status":status,"data":result.get("data",{})},
            "outcome":{"code":code,"reason":reason,"basis":"stored server trace"},
            "duration_ms":trace.get("duration_ms"),
        }

    def inspect_run(self, owner, run_id):
        run=self.get_run(owner,run_id)
        with self.connect() as db:
            rows=db.execute("SELECT id,verdict,reason,expected,created FROM run_feedback WHERE run_id=? AND owner=? ORDER BY created,id",(run_id,owner)).fetchall()
        feedback=[{"feedback_id":r["id"],"verdict":r["verdict"],"reason":r["reason"],"expected_behavior":json.loads(r["expected"]),"created_at":datetime.fromtimestamp(r["created"],timezone.utc).isoformat()} for r in rows]
        return {"run":run,"steps":[self._step_inspection(t) for t in run["traces"]],"feedback":feedback,"cause_boundary":"Reasons use stored calls, typed results, status codes and server errors. They do not infer a model's hidden reasoning or an unobserved root cause."}

    def add_feedback(self, owner, run_id, verdict, reason, expected):
        self.get_run(owner,run_id)
        feedback_id=str(uuid.uuid4())
        created=time.time()
        with self.connect() as db:
            db.execute("INSERT INTO run_feedback VALUES (?,?,?,?,?,?,?)",(feedback_id,run_id,owner,verdict,reason,json.dumps(expected,sort_keys=True,separators=(",",":")),created))
        return {"feedback_id":feedback_id,"run_id":run_id,"verdict":verdict,"reason":reason,"expected_behavior":expected,"created_at":datetime.fromtimestamp(created,timezone.utc).isoformat(),"immutable":True}

    def export_regression(self, owner, run_id, feedback_id):
        run=self.get_run(owner,run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review=db.execute("SELECT * FROM run_feedback WHERE id=? AND run_id=? AND owner=?",(feedback_id,run_id,owner)).fetchone()
            if review is None:
                raise PolicyError("Feedback provenance unavailable to this principal")
            if review["verdict"]!="incorrect":
                raise PolicyError("Only reviewed failures can be exported")
            previous=db.execute("SELECT fixture FROM regression_fixtures WHERE feedback_id=? AND owner=?",(feedback_id,owner)).fetchone()
            if previous:
                return json.loads(previous[0])
            expected=json.loads(review["expected"])
            first=next((trace for trace in run["traces"] if "result" in trace),{})
            observed={
                "tool":first.get("result",{}).get("tool"),
                "status":first.get("result",{}).get("status"),
                "arguments":first.get("call",{}).get("arguments"),
                "stop_reason":run.get("stop_reason"),
            }
            if expected==observed:
                raise PolicyError("Expected behavior matches the observed run; mark it correct or record a real mismatch")
            calls=[t["call"] for t in run["traces"] if "call" in t]
            unavailable=sorted({t["result"]["tool"] for t in run["traces"] if t.get("result",{}).get("status")=="unavailable"})
            fixture_id=str(uuid.uuid4())
            run_blob=json.dumps(run,sort_keys=True,separators=(",",":"))
            feedback_record={"id":review["id"],"run_id":run_id,"owner":owner,"verdict":review["verdict"],"reason":review["reason"],"expected":expected}
            feedback_blob=json.dumps(feedback_record,sort_keys=True,separators=(",",":"))
            fixture={
                "schema_version":1,"fixture_id":fixture_id,"split":"development_reviewed_failure",
                "source":{"run_id":run_id,"run_sha256":hashlib.sha256(run_blob.encode()).hexdigest(),"feedback_id":feedback_id,"feedback_sha256":hashlib.sha256(feedback_blob.encode()).hexdigest()},
                "source_snapshot":{"run":run,"feedback":feedback_record},
                "replay":{"owner":owner,"prompt":run.get("prompt", ""),"calls":calls,"unavailable":unavailable},
                "expected":expected,"review_reason":review["reason"],
                "limitations":["Synthetic local trace","Development regression fixture; never part of held-out evaluation","Export records evidence and does not retry or approve an action"],
            }
            blob=json.dumps(fixture,sort_keys=True,separators=(",",":"))
            db.execute("INSERT INTO regression_fixtures VALUES (?,?,?,?,?,?)",(fixture_id,feedback_id,run_id,owner,blob,time.time()))
        return fixture


class Baseline:
    name = "deterministic-baseline"

    def next(self, prompt, traces):
        if traces:
            return {"tool": "finish", "arguments": {"answer": "Inspect the verified tool result. Any proposed action requires your explicit approval."}}, {}
        task = re.search(r"\b([a-z]+-task)\b", prompt)
        if task:
            return {"tool": "get_task", "arguments": {"task_id": task.group(1)}}, {}
        if re.search(r"\b(import|upload)\b", prompt, re.I):
            values = [float(x) for x in re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", prompt)]
            return {"tool": "propose_import", "arguments": {"values": values}}, {}
        return {"tool": "search_docs", "arguments": {"query": prompt[:300]}}, {}


class Assistant:
    def __init__(self, store, provider=None, unavailable=(), max_steps=3, timeout=20):
        self.store = store
        self.provider = provider or Baseline()
        self.unavailable = set(unavailable)
        self.max_steps = min(max_steps, 3)
        self.timeout = min(timeout, 30)

    def dispatch(self, owner, raw):
        call = ToolCall.model_validate(raw)
        if call.tool in self.unavailable:
            return ToolResult(tool=call.tool, status="unavailable", data={"error": "Tool unavailable; no action performed"})
        if call.tool == "get_task":
            args = TaskArgs.model_validate(call.arguments)
            data = self.store.task(owner, args.task_id)
        elif call.tool == "propose_import":
            args = ImportArgs.model_validate(call.arguments)
            data = self.store.propose(owner, args.values)
        elif call.tool == "search_docs":
            args = SearchArgs.model_validate(call.arguments)
            docs = json.loads((ROOT / "data/docs.json").read_text())
            terms = set(re.findall(r"\w+", args.query.lower()))
            ranked = sorted(docs, key=lambda d: (-len(terms & set(re.findall(r"\w+", (d['title'] + ' ' + d['text']).lower()))), d["id"]))
            data = {"documents": ranked[:2], "trust": "untrusted_content"}
        else:
            args = FinishArgs.model_validate(call.arguments)
            data = {"answer": args.answer, "trust": "model_generated_unverified"}
        return ToolResult(tool=call.tool, status="ok", data=data)

    def run(self, owner, prompt):
        started = time.monotonic()
        created_at = datetime.now(timezone.utc).isoformat()
        traces = []
        stop = "step_limit"
        for index in range(self.max_steps):
            if time.monotonic() - started >= self.timeout:
                stop = "deadline"
                break
            try:
                step_started=time.monotonic()
                raw, usage = self.provider.next(prompt, traces)
                if time.monotonic() - started >= self.timeout:
                    stop = "deadline"
                    break
                try:
                    result = self.dispatch(owner, raw)
                except PolicyError as exc:
                    result = ToolResult(tool=str(raw.get("tool", "unknown")), status="denied", data={"error": str(exc)})
                except (ValidationError, TypeError, ValueError) as exc:
                    result = ToolResult(tool=str(raw.get("tool", "unknown")), status="invalid", data={"error": "Tool schema rejected"})
                traces.append({"step": index + 1, "call": raw, "result": result.model_dump(), "usage": usage, "duration_ms":round((time.monotonic()-step_started)*1000,3)})
                if result.status != "ok" or result.tool == "finish":
                    stop = result.status if result.status != "ok" else "finished"
                    break
            except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
                stop = "provider_unavailable"
                traces.append({"step": index + 1, "error": type(exc).__name__, "duration_ms":round((time.monotonic()-step_started)*1000,3)})
                break
        response = {"run_id": str(uuid.uuid4()), "created_at":created_at, "prompt":prompt, "provider": self.provider.name, "stop_reason": stop, "traces": traces, "latency_ms": round((time.monotonic() - started) * 1000, 3), "execution": "proposals_only_until_explicit_approval"}
        self.store.save_run(owner, response)
        return response
