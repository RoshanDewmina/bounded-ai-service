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

Agent-generated implementation does not establish personal authorship, leadership, production responsibility or understanding. Resume wording and ownership remain user-review pending.
