import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from mabeyn.db.models import Chunk, SecurityAction
from mabeyn.db.session import tenant_session
from mabeyn.guard import canary as canary_module
from mabeyn.guard.classifier import InjectionClassifier, Verdict
from mabeyn.guard.events import record_event, snippet
from mabeyn.guard.pii import mask_pii
from mabeyn.guard.rules import FLAG, inspect_input, override_match
from mabeyn.guard.settings import GuardSettings, load_guard_settings

FLAG_SHARE = 0.6
_SQUASHED = " -()."


def _squash(value: str) -> str:
    return value.lower().translate({ord(c): None for c in _SQUASHED})


@dataclass(frozen=True)
class FirewallConfig:
    """Which layers run. Evaluations switch them off to measure what each one buys."""

    input: bool = True
    documents: bool = True
    tools: bool = True
    output: bool = True

    @classmethod
    def disabled(cls) -> "FirewallConfig":
        return cls(False, False, False, False)


@dataclass(frozen=True)
class InputDecision:
    allowed: bool
    category: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class OutputDecision:
    text: str
    changed: bool = False
    leaked: bool = False


class Firewall:
    """The layers of the LLM firewall that need a model, tenant settings or the event log."""

    def __init__(
        self, classifier: InjectionClassifier, config: FirewallConfig | None = None
    ) -> None:
        self.classifier = classifier
        self.config = config or FirewallConfig()

    async def settings(self, tenant_id: uuid.UUID) -> GuardSettings:
        return await load_guard_settings(tenant_id)

    async def active(self, tenant_id: uuid.UUID, layer: str) -> bool:
        if not getattr(self.config, layer):
            return False
        return (await self.settings(tenant_id)).firewall_enabled

    async def inspect_input(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID | None, raw: str, clean: str
    ) -> InputDecision:
        settings = await self.settings(tenant_id)
        if not (self.config.input and settings.firewall_enabled):
            return InputDecision(True)

        inspection = inspect_input(raw)
        for finding in inspection.findings:
            if finding.severity == FLAG:
                await record_event(
                    tenant_id,
                    conversation_id,
                    "input",
                    finding.rule,
                    SecurityAction.FLAGGED,
                    {"detail": finding.detail},
                )
        if inspection.blocked:
            category = inspection.rule or "rule"
            await record_event(
                tenant_id,
                conversation_id,
                "input",
                category,
                SecurityAction.BLOCKED,
                {"stage": "rules", "text": snippet(clean)},
            )
            return InputDecision(False, category, 1.0)

        verdict = await self.classifier.classify("\n".join([clean, *inspection.decoded]), "message")
        if verdict.score >= settings.injection_threshold:
            await record_event(
                tenant_id,
                conversation_id,
                "input",
                verdict.category,
                SecurityAction.BLOCKED,
                {"stage": "classifier", "score": verdict.score, "text": snippet(clean)},
            )
            return InputDecision(False, verdict.category, verdict.score)
        if verdict.score >= settings.injection_threshold * FLAG_SHARE:
            await record_event(
                tenant_id,
                conversation_id,
                "input",
                verdict.category,
                SecurityAction.FLAGGED,
                {"stage": "classifier", "score": verdict.score},
            )
        return InputDecision(True, None, verdict.score)

    async def _published(self, tenant_id: uuid.UUID, value: str) -> bool:
        """True when the tenant's own documents contain the value, like a support address."""
        needle = _squash(value)
        async with tenant_session(tenant_id) as session:
            found = await session.scalar(
                select(Chunk.id)
                .where(
                    func.strpos(func.translate(func.lower(Chunk.content), _SQUASHED, ""), needle)
                    > 0,
                    Chunk.flagged.is_(False),
                )
                .limit(1)
            )
        return found is not None

    async def scan_chunk(self, text: str) -> Verdict:
        """Rate a document passage for instructions aimed at an AI assistant."""
        if override_match(text):
            return Verdict(1.0, "override", "known override phrase")
        return await self.classifier.classify(text, "document")

    async def filter_output(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        text: str,
        replacement: str,
    ) -> OutputDecision:
        """Replace replies that leak the canary and mask validated personal data."""
        if not await self.active(tenant_id, "output"):
            return OutputDecision(text)
        canary = canary_module.canary_for(tenant_id)
        if canary_module.leaked(text, canary):
            await record_event(
                tenant_id, conversation_id, "output", "canary_leak", SecurityAction.BLOCKED
            )
            return OutputDecision(replacement, True, True)
        masked = mask_pii(text)
        if masked.changed:
            published = {v for v in masked.values if await self._published(tenant_id, v)}
            if published:
                masked = mask_pii(text, frozenset(published))
        if masked.changed:
            await record_event(
                tenant_id,
                conversation_id,
                "output",
                "pii_masked",
                SecurityAction.FLAGGED,
                {"kinds": sorted(set(masked.kinds))},
            )
            return OutputDecision(masked.text, True)
        return OutputDecision(text)
