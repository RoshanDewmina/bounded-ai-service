import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .core import Assistant, Baseline, PolicyError, ROOT, Store


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=500)
    provider: Literal["baseline", "model"] = "baseline"


def create_app(path=None, model=None, tokens=None):
    configured = tokens or json.loads(os.environ.get("ASSISTANT_TOKENS", '{"demo-alpha":"alpha","demo-beta":"beta"}'))
    if not isinstance(configured, dict) or not configured or any(not isinstance(k, str) or not k or v not in ("alpha", "beta") for k, v in configured.items()):
        raise ValueError("Invalid principal configuration")
    if os.environ.get("PUBLIC_MODE") == "1" and any(len(t) < 32 or t.startswith("demo-") for t in configured):
        raise ValueError("Public mode requires operator-supplied credentials")
    store = Store(path or os.environ.get("ASSISTANT_DB", "assistant.sqlite"))
    bearer = HTTPBearer(auto_error=False)

    def principal(credentials: HTTPAuthorizationCredentials | None = Security(bearer)):
        if credentials is None or credentials.credentials not in configured:
            raise HTTPException(401, "Valid bearer credential required")
        return configured[credentials.credentials]

    @asynccontextmanager
    async def lifespan(app):
        yield
        if model:
            model.close()

    app = FastAPI(title="Bounded AI service", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        try:
            with store.connect() as db:
                db.execute("SELECT 1 FROM tasks LIMIT 1")
        except Exception:
            raise HTTPException(503, "Store unavailable") from None
        return {"status": "ok", "service": "bounded-ai-service", "version": "0.1.0", "model_available": model is not None}

    @app.post("/runs")
    def run(body: RunRequest, owner=Depends(principal)):
        if body.provider == "model" and model is None:
            raise HTTPException(503, "Local model is not loaded; baseline remains available")
        try:
            return Assistant(store, model if body.provider == "model" else Baseline()).run(owner, body.prompt)
        except PolicyError as exc:
            raise HTTPException(429, str(exc)) from None

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, owner=Depends(principal)):
        try:
            return store.get_run(owner, run_id)
        except PolicyError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/proposals/{proposal_id}/approve")
    def approve(proposal_id: str, owner=Depends(principal)):
        try:
            return store.approve(owner, proposal_id)
        except PolicyError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/tasks/{task_id}")
    def task(task_id: str, owner=Depends(principal)):
        try:
            return store.task(owner, task_id)
        except PolicyError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (ROOT / "src/bounded_ai/index.html").read_text()

    return app


def main():
    import uvicorn
    provider = None
    if os.environ.get("LOAD_LOCAL_MODEL") == "1":
        from .local_model import LocalModel
        provider = LocalModel(json.loads((ROOT / "data/model-manifest.json").read_text())["revision"])
    uvicorn.run(create_app(model=provider), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8112")))


if __name__ == "__main__":
    main()
