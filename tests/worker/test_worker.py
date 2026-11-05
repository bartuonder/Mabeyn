import uuid

from apps.worker import main as worker
from mabeyn.db.models import DocumentStatus
from mabeyn.queue import INGEST_JOB


def test_job_name_matches_worker_function():
    assert worker.ingest.__name__ == INGEST_JOB
    assert worker.ingest in worker.WorkerSettings.functions


async def test_worker_delegates_to_pipeline(monkeypatch):
    seen = {}

    async def fake_ingest(
        tenant_id: uuid.UUID, document_id: uuid.UUID, firewall=None
    ) -> DocumentStatus:
        seen["ids"] = (tenant_id, document_id)
        seen["firewall"] = firewall
        return DocumentStatus.READY

    monkeypatch.setattr(worker, "ingest_document", fake_ingest)
    tenant, document = uuid.uuid4(), uuid.uuid4()
    assert await worker.ingest({"firewall": "fw"}, str(tenant), str(document)) == "ready"
    assert seen["ids"] == (tenant, document) and seen["firewall"] == "fw"


def test_memory_jobs_are_scheduled():
    scheduled = {job.coroutine.__name__ for job in worker.WorkerSettings.cron_jobs}
    assert {"mine_idle_conversations", "sweep_expired_memories"} <= scheduled


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


def test_approval_expiry_is_scheduled():
    scheduled = {job.coroutine.__name__ for job in worker.WorkerSettings.cron_jobs}
    assert "expire_pending_approvals" in scheduled


async def test_the_expiry_job_uses_the_agent_runtime(monkeypatch):
    seen = {}

    async def fake_expire(service):
        seen["service"] = service
        return 2

    class Runtime:
        service = object()

    monkeypatch.setattr(worker, "expire_stale_approvals", fake_expire)
    ctx = {"runtime": Runtime()}
    assert await worker.expire_pending_approvals(ctx) == 2
    assert seen["service"] is Runtime.service
