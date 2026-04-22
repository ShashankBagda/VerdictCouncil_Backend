"""Transactional-outbox dispatcher + arq worker for pipeline jobs.

The web process never enqueues arq jobs directly. Endpoints INSERT a
`pipeline_jobs` row inside the same transaction that flips case /
scenario / stability status; the dispatcher (running inside the arq
worker process) claims pending rows via `FOR UPDATE SKIP LOCKED`,
hands them to arq, and flips them to `dispatched`. The arq task is
idempotent — re-delivery is safe because the task checks the job
row's status before acting and flips it to `completed`/`failed` on
exit.
"""
