"""arq WorkerSettings — launched via `arq src.workers.worker_settings.WorkerSettings`.

One process runs both the arq task executor and the outbox
dispatcher (spawned from `on_startup`). Horizontal scaling is safe
because `FOR UPDATE SKIP LOCKED` in the dispatcher's claim SQL
guarantees no row is double-dispatched.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from src.shared.config import settings
from src.workers.dispatcher import shutdown, startup
from src.workers.tasks import (
    run_case_pipeline_job,
    run_stability_computation_job,
    run_whatif_scenario_job,
)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        run_case_pipeline_job,
        run_whatif_scenario_job,
        run_stability_computation_job,
    ]
    on_startup = startup
    on_shutdown = shutdown
    # Pipeline runs can exceed the arq default (5 min). 900s matches
    # the mesh runner's overall SLA ceiling — past this the job is
    # treated as worker-crashed and the outbox stuck-recovery loop
    # will flip it back to `pending`.
    job_timeout = 900
    max_jobs = 10
    keep_result = 0
