import json
from dataclasses import dataclass
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


@dataclass(frozen=True)
class Gold:
    doc: str
    section: str


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    expected_answer: str
    answerable: bool
    gold: tuple[Gold, ...]
    category: str
    set: str


def load_questions(name: str = "rag.jsonl", sets: tuple[str, ...] | None = None) -> list[Question]:
    questions = []
    for line in (DATASETS_DIR / name).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if sets is not None and row["set"] not in sets:
            continue
        questions.append(
            Question(
                id=row["id"],
                question=row["question"],
                expected_answer=row["expected_answer"],
                answerable=row["answerable"],
                gold=tuple(Gold(**g) for g in row["gold"]),
                category=row["category"],
                set=row["set"],
            )
        )
    return questions
