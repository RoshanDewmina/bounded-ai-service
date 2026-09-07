import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
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
            ''')
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
            db.execute("INSERT INTO runs VALUES (?,?,?)", (response["run_id"], owner, json.dumps(response)))

    def get_run(self, owner, run_id):
        with self.connect() as db:
            row = db.execute("SELECT response FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if not row:
            raise PolicyError("Run unavailable to this principal")
        return json.loads(row[0])


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
        traces = []
        stop = "step_limit"
        for index in range(self.max_steps):
            if time.monotonic() - started >= self.timeout:
                stop = "deadline"
                break
            try:
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
                traces.append({"step": index + 1, "call": raw, "result": result.model_dump(), "usage": usage})
                if result.status != "ok" or result.tool == "finish":
                    stop = result.status if result.status != "ok" else "finished"
                    break
            except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
                stop = "provider_unavailable"
                traces.append({"step": index + 1, "error": type(exc).__name__})
                break
        response = {"run_id": str(uuid.uuid4()), "provider": self.provider.name, "stop_reason": stop, "traces": traces, "latency_ms": round((time.monotonic() - started) * 1000, 3), "execution": "proposals_only_until_explicit_approval"}
        self.store.save_run(owner, response)
        return response
