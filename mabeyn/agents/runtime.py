from contextlib import AsyncExitStack

from mabeyn.agents.checkpoint import open_checkpointer
from mabeyn.agents.service import AgentService, SupervisorService
from mabeyn.llm.client import get_llm
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.tools.service import ToolRunner


class AgentRuntime:
    """Owns the checkpointer pool for the lifetime of the API process."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.service: SupervisorService | None = None

    async def start(self) -> None:
        saver = await self._stack.enter_async_context(open_checkpointer())
        llm = get_llm()
        self.service = SupervisorService(llm, GraphRag(llm, RagConfig()), ToolRunner(), saver)

    async def stop(self) -> None:
        await self._stack.aclose()
        self.service = None


runtime = AgentRuntime()


def get_agent_service() -> AgentService:
    if runtime.service is None:
        raise RuntimeError("the agent runtime has not been started")
    return runtime.service
