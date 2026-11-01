from evals.datasets import load_questions
from mabeyn.core.tracking import export_tracking_env, langsmith_enabled

DATASETS = {
    "rag.jsonl": "mabeyn-kargoshop-rag",
    "rag_en.jsonl": "mabeyn-kargoshop-rag-en",
}


def upload(file_name: str, dataset_name: str) -> int:
    from langsmith import Client

    client = Client()
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name, description=f"Golden questions from {file_name} for the KargoShop tenant"
        )
    known = {
        example.metadata["id"]
        for example in client.list_examples(dataset_id=dataset.id)
        if example.metadata and "id" in example.metadata
    }
    fresh = [q for q in load_questions(file_name) if q.id not in known]
    if fresh:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[{"question": q.question} for q in fresh],
            outputs=[{"expected_answer": q.expected_answer} for q in fresh],
            metadata=[
                {
                    "id": q.id,
                    "category": q.category,
                    "answerable": q.answerable,
                    "set": q.set,
                    "gold": [{"doc": g.doc, "section": g.section} for g in q.gold],
                }
                for q in fresh
            ],
        )
    return len(fresh)


def main() -> None:
    export_tracking_env()
    if not langsmith_enabled():
        print("LangSmith is not configured; set LANGSMITH_API_KEY and LANGSMITH_TRACING=true")
        return
    for file_name, dataset_name in DATASETS.items():
        print(f"{dataset_name}: uploaded {upload(file_name, dataset_name)} new examples")


if __name__ == "__main__":
    main()
