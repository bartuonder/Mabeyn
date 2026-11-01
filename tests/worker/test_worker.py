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
