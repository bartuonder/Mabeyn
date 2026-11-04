from contextlib import AsyncExitStack

from mabeyn.agents.checkpoint import open_checkpointer
from mabeyn.agents.service import AgentService, SupervisorService
from mabeyn.cache.answers import AnswerCache, get_kv
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall
from mabeyn.guard.policies import ToolGuard
from mabeyn.llm.client import get_llm
from mabeyn.memory.service import MemoryService
from mabeyn.memory.store import open_memory_store
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.tools.service import ToolRunner


class AgentRuntime:
    """Owns the checkpointer pool for the lifetime of the API process."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.service: SupervisorService | None = None
        self.memory: MemoryService | None = None

    async def start(self) -> None:
        saver = await self._stack.enter_async_context(open_checkpointer())
        store = await self._stack.enter_async_context(open_memory_store())
        llm = get_llm()
        runner = ToolRunner()
        self.service = SupervisorService(
            llm,
            GraphRag(llm, RagConfig(canary_guard=True)),
            runner,
            saver,
            store,
            Firewall(LlmInjectionClassifier(llm)),
            ToolGuard(runner),
            AnswerCache(llm, get_kv()),
        )
        self.memory = MemoryService(llm, store)

    async def stop(self) -> None:
        await self._stack.aclose()
        self.service = None
        self.memory = None


runtime = AgentRuntime()


def get_agent_service() -> AgentService:
    if runtime.service is None:
        raise RuntimeError("the agent runtime has not been started")
    return runtime.service


def get_memory_service() -> MemoryService:
    if runtime.memory is None:
        raise RuntimeError("the agent runtime has not been started")
    return runtime.memory
