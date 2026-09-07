# Explain and challenge the assistant

The user outcome is an inspectable proposed import followed by explicit approval. A model that outputs a valid tool call still has no authority: server credentials determine principal, the schema determines allowed input, and the separate approval transaction determines execution.

Be ready to trace one request through API authentication, provider output, typed parsing, owner filtering, proposal persistence and approval. Explain why prompt instructions are insufficient for authorization. Compare the exact-match baseline with actual model cases, including cases where the baseline wins.

Why SQLite? A single local transaction can atomically commit a task and its approval receipt. A remote workflow system needs an outbox, idempotent downstream consumer and reconciliation for ambiguous delivery; the current local guarantee does not prove that. A timeout cancels the inference process before any returned tool is dispatched. Side-effect retries need stronger semantics than read retries.

Exercises (personal mastery remains pending):

1. Reproduce the cross-user attack and show the owner predicate that denies it.
2. Race 16 approvals; inspect one approval row and one new task.
3. Add a deliberately malicious retrieved paragraph, then attempt an execution tool. Explain both the model trace and server rejection.
4. Restart the service between proposal and approval. Explain what persisted and which model resources did not.
5. Compare all held-out model failures against the baseline without modifying the held-out labels or tuning to them.
6. Design a remote durable-workflow adapter: identify where a timeout makes delivery unknown, and where a dedupe key must live.
7. Identify current demo limits and propose a production identity, quota and retention model without claiming those exist today.
8. Trace `GET /runs` and explain why both the SQL owner predicate and authenticated principal matter. Then compare the immutable run record with append-only reviewer feedback.
9. Export a reviewed denial, change its recorded task argument, and replay it. Explain why the exact-match scorer fails and why neither export nor replay approves a proposal.
10. Explain the evidence boundary: server status and stored errors support the inspector's failure reason; they do not reveal the model's hidden reasoning or prove a root cause outside the trace.

## Concise project explanation

The run inspector closes a debugging loop around the existing bounded agent. An authenticated owner can list recent runs, inspect typed calls and server-observed outcomes, and add a human correct/incorrect judgment with a reason. Reviewed failures export to provenance-bound development fixtures that replay exact calls and score tool, status, arguments and stop reason. The workflow never mutates the held-out evaluation set and inspection/export never retries a tool or crosses the separate approval endpoint. It is a local synthetic proof: it does not show improved model quality, real-user impact or production operations.

Agent-generated implementation does not establish personal authorship, leadership, production responsibility or understanding. Resume wording and ownership remain user-review pending.
