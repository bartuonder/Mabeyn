"""Keeps the generated parts of docs/architecture.md in step with the code.

uv run python -m mabeyn.docs_sync          rewrite the generated blocks
uv run python -m mabeyn.docs_sync --check  exit with an error when they are out of date
"""

import inspect
import re
import sys
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

from mabeyn.agents import action
from mabeyn.agents import prompts as agent_prompts
from mabeyn.agents.supervisor import AgentDeps, build_supervisor_graph
from mabeyn.core.config import Settings
from mabeyn.ingestion.chunk import ChunkConfig
from mabeyn.rag import graph as rag_graph_module
from mabeyn.rag import prompts as rag_prompts
from mabeyn.rag import rerank
from mabeyn.rag.graph import RagConfig, build_rag_graph
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
BLOCK = re.compile(
    r"<!-- generated:(?P<name>[a-z-]+) -->\n(?P<body>.*?)<!-- /generated -->", re.DOTALL
)


def _unused() -> Any:
    """The graphs are only inspected, never run, so their dependencies can be empty."""
    return cast(Any, None)


def compiled_rag_graph() -> Any:
    return build_rag_graph(_unused(), RagConfig())


def compiled_supervisor_graph() -> Any:
    return build_supervisor_graph(AgentDeps(_unused(), _unused(), ToolRunner())).compile()


def node_names(compiled: Any) -> list[str]:
    return [name for name in compiled.get_graph().nodes if not name.startswith("__")]


def _mermaid(compiled: Any) -> str:
    diagram: str = compiled.get_graph().draw_mermaid()
    return "```mermaid\n" + diagram.strip() + "\n```\n"


def rag_graph() -> str:
    return _mermaid(compiled_rag_graph())


def supervisor_graph() -> str:
    return _mermaid(compiled_supervisor_graph())


def _default(model_field: str) -> Any:
    default: Any = Settings.model_fields[model_field].default
    return default


def facts() -> str:
    rag = {f.name: f.default for f in fields(RagConfig)}
    chunk = {f.name: f.default for f in fields(ChunkConfig)}
    retries = inspect.signature(HttpToolExecutor.__init__).parameters["max_retries"].default
    rows = [
        ("Prompt versions", f"`{rag_prompts.PROMPT_VERSION}`, `{agent_prompts.PROMPT_VERSION}`"),
        ("Answer and action model", f"`{_default('llm_model')}`"),
        (
            "Small model (routing, rewriting, grading, reranking, checks)",
            f"`{_default('llm_model_small')}`",
        ),
        ("Judge model (evaluation)", f"`{_default('judge_model')}`"),
        ("Embedding model", f"`{_default('embedding_model')}`"),
        ("Chunk size", f"{chunk['min_tokens']}–{chunk['max_tokens']} tokens"),
        ("Retrieval", f"`{rag['retrieval']}`, {rag['pool']} candidates"),
        ("Reranker", f"{'on' if rag['rerank'] else 'off'}, keeps {rag['final_k']}"),
        ("Relevance grading", "on" if rag["crag"] else "off"),
        ("Faithfulness check", "on" if rag["faithfulness"] else "off"),
        ("Query rewrites after a failed grading", str(rag["max_rewrites"])),
        ("Regenerations after a failed faithfulness check", str(rag["max_regenerations"])),
        (
            "Passage length shown to grader and reranker (characters)",
            f"{rag_graph_module.GRADE_PASSAGE_CHARS} and {rerank.PASSAGE_CHARS}",
        ),
        ("Tool calls per request", str(action.MAX_TOOL_CALLS)),
        ("Consecutive tool failures before a human takes over", str(action.MAX_CONSECUTIVE_ERRORS)),
        ("History messages given to the action agent", str(action.HISTORY_MESSAGES)),
        ("Tool timeout (seconds)", str(_default("tool_timeout_seconds"))),
        ("Tool response limit (bytes)", str(_default("tool_max_response_bytes"))),
        ("Retries for `GET` tools", str(retries)),
        ("Customer token lifetime (minutes)", str(_default("customer_token_ttl_minutes"))),
    ]
    lines = ["| Setting | Value |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    return "\n".join(lines) + "\n"


RENDERERS: dict[str, Callable[[], str]] = {
    "rag-graph": rag_graph,
    "supervisor-graph": supervisor_graph,
    "facts": facts,
}


def render(text: str) -> str:
    """Return the document with every generated block rewritten from the code."""

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in RENDERERS:
            raise KeyError(f"unknown generated block: {name}")
        return f"<!-- generated:{name} -->\n{RENDERERS[name]()}<!-- /generated -->"

    return BLOCK.sub(replace, text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    current = DOC.read_text(encoding="utf-8")
    updated = render(current)
    if "--check" in args:
        if updated != current:
            print(f"{DOC.name} is out of date; run: uv run python -m mabeyn.docs_sync")
            return 1
        print(f"{DOC.name} is up to date")
        return 0
    if updated != current:
        DOC.write_text(updated, encoding="utf-8", newline="\n")
        print(f"updated {DOC.name}")
    else:
        print(f"{DOC.name} already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
