.PHONY: setup test demo benchmark model-demo model-eval
setup:
	uv sync --frozen
test:
	uv run pytest -q
demo:
	uv run python3 -m bounded_ai.api
benchmark:
	uv run python3 -m bounded_ai.evaluate --provider baseline
model-demo:
	LOAD_LOCAL_MODEL=1 uv run --extra model python3 -m bounded_ai.api
model-eval:
	uv run --extra model python3 -m bounded_ai.evaluate --provider model
