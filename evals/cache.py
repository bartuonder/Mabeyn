import hashlib
import json
import os
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from mabeyn.core.config import get_settings
from mabeyn.ingestion.chunk import count_tokens
from mabeyn.llm import usage
from mabeyn.llm.client import LLM, ChatMessage, ChatResult, ToolCallRequest, ToolChatResult

CACHE_DIR = Path(os.environ.get("EVAL_CACHE_DIR") or Path(__file__).resolve().parent / ".cache")


class CachingLLM:
    """Disk cache for deterministic calls. Hits replay tokens and latency but spend nothing."""

    def __init__(self, inner: LLM, directory: Path = CACHE_DIR) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0

    def _path(self, kind: str, payload: Any) -> Path:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return self.directory / kind / digest[:2] / f"{digest}.json"

    def _load(self, path: Path) -> dict[str, Any] | None:
        if path.exists():
            self.hits += 1
            loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return loaded
        self.misses += 1
        return None

    @staticmethod
    def _store(path: Path, entry: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        model = model or get_settings().llm_model
        path = self._path("chat", [model, temperature, max_tokens, json_mode, list(messages)])
        if entry := self._load(path):
            usage.record(model, entry["in"], entry["out"], billed=False)
            usage.adjust_latency(entry["latency"])
            return ChatResult(entry["text"], entry["in"], entry["out"])

        started = time.perf_counter()
        result = await self.inner.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        entry = {
            "text": result.text,
            "in": result.input_tokens,
            "out": result.output_tokens,
            "latency": time.perf_counter() - started,
        }
        self._store(path, entry)
        return result

    async def chat_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolChatResult:
        model = model or get_settings().llm_model
        path = self._path("tools", [model, temperature, max_tokens, list(tools), list(messages)])
        if entry := self._load(path):
            usage.record(model, entry["in"], entry["out"], billed=False)
            usage.adjust_latency(entry["latency"])
            calls = tuple(ToolCallRequest(**c) for c in entry["calls"])
            return ToolChatResult(entry["text"], calls, entry["in"], entry["out"])

        started = time.perf_counter()
        result = await self.inner.chat_with_tools(
            messages, tools, model=model, temperature=temperature, max_tokens=max_tokens
        )
        self._store(
            path,
            {
                "text": result.text,
                "calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in result.tool_calls
                ],
                "in": result.input_tokens,
                "out": result.output_tokens,
                "latency": time.perf_counter() - started,
            },
        )
        return result

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        model = model or get_settings().llm_model
        path = self._path("stream", [model, temperature, max_tokens, list(messages)])
        if entry := self._load(path):
            usage.record(model, entry["in"], entry["out"], billed=False)
            usage.adjust_latency(entry["latency"])
            yield entry["text"]
            return

        meter = usage.current_meter()
        before = (meter.input_tokens, meter.output_tokens) if meter else (0, 0)
        started = time.perf_counter()
        pieces: list[str] = []
        async for piece in self.inner.stream(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        ):
            pieces.append(piece)
            yield piece
        after = (meter.input_tokens, meter.output_tokens) if meter else (0, 0)
        self._store(
            path,
            {
                "text": "".join(pieces),
                "in": after[0] - before[0],
                "out": after[1] - before[1],
                "latency": time.perf_counter() - started,
            },
        )

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        model = model or get_settings().embedding_model
        dimensions = get_settings().embedding_dimensions
        paths = [self._path("embed", [model, dimensions, t]) for t in texts]
        vectors: list[list[float] | None] = []
        missing: list[int] = []
        for index, path in enumerate(paths):
            entry = self._load(path)
            vectors.append(entry["vector"] if entry else None)
            if entry is None:
                missing.append(index)
            else:
                usage.record(model, count_tokens(texts[index]), 0, billed=False)
        if missing:
            fresh = await self.inner.embed([texts[i] for i in missing], model=model)
            for index, vector in zip(missing, fresh, strict=True):
                vectors[index] = vector
                self._store(paths[index], {"vector": vector})
        return [v for v in vectors if v is not None]
