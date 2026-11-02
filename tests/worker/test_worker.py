import uuid

from apps.worker import main as worker
from mabeyn.db.models import DocumentStatus
from mabeyn.queue import INGEST_JOB


def test_job_name_matches_worker_function():
    assert worker.ingest.__name__ == INGEST_JOB
    assert worker.ingest in worker.WorkerSettings.functions


async def test_worker_delegates_to_pipeline(monkeypatch):
    seen = {}

    async def fake_ingest(tenant_id: uuid.UUID, document_id: uuid.UUID) -> DocumentStatus:
        seen["ids"] = (tenant_id, document_id)
        return DocumentStatus.READY

    monkeypatch.setattr(worker, "ingest_document", fake_ingest)
    tenant, document = uuid.uuid4(), uuid.uuid4()
    assert await worker.ingest({}, str(tenant), str(document)) == "ready"
    assert seen["ids"] == (tenant, document)


def test_memory_jobs_are_scheduled():
    scheduled = {job.coroutine.__name__ for job in worker.WorkerSettings.cron_jobs}
    assert scheduled == {"mine_idle_conversations", "sweep_expired_memories"}


async def test_memory_jobs_use_the_shared_memory_service(monkeypatch):
    seen = {}

    async def fake_extract(service):
        seen["service"] = service
        return 3

    class Store:
        async def sweep(self):
            return 2

    class Service:
        store = Store()

    monkeypatch.setattr(worker, "extract_idle", fake_extract)
    ctx = {"memory": Service()}
    assert await worker.mine_idle_conversations(ctx) == 3
    assert seen["service"] is ctx["memory"]
    assert await worker.sweep_expired_memories(ctx) == 2
