# ADR-0003: Defense layers of the LLM firewall

- Status: accepted
- Date: 2026-11-03

## Context

The assistant reads text from three untrusted places: customer messages, retrieved documents and tool results. It can act on a customer's behalf and it holds instructions, notes and tenant data that must not leave. A single filter in front of the model fails the first time an attack arrives some other way, for example through an uploaded document or through a tool argument.

## Decision

Independent layers, each logging to `security_events`, each measured separately on a red-team set.

1. **Input.** Cheap rules run before any model: hidden and tag characters, base64 and hex payloads (decoded and checked again) and known override phrases in English, Turkish and German. A small model then rates the message from 0 to 1 and the tenant's threshold decides (default 0.7); it also reads the customer's last four messages as data, because a trigger agreed in one message and fired in the next looks harmless alone. An unreadable rating blocks. A blocked message gets a fixed bilingual reply, so blocked text never reaches a model again, and it is kept but marked `blocked` so summaries and memory ignore it.
2. **Indirect injection.** Every passage is rated with a second prompt at ingestion, and passages above the threshold are flagged and excluded from vector and full-text retrieval. At answer time retrieved text sits in delimited `<source>` blocks that are declared untrusted, and forged tags inside a source are neutralised so a document cannot close its own block.
3. **Tools.** A per-tenant allowlist hides tools from the action agent. Argument policies are data on the tool definition (`owned_record`, `max_amount`, `required_status`, `time_window`). Each fetches the real record through the tenant's own lookup tool, with the customer's identity injected by the platform, so KargoShop's refund is checked for ownership, amount, delivery status and the 30 day window. A policy whose lookup tool is missing blocks the call instead of passing it. A rate limit counts write and sensitive calls per customer. Policies run before an approval is requested and again before an approved call runs.
4. **Output.** A per-tenant canary token sits in the prompts of the agents that see instructions; a reply that contains it, even broken up, is replaced. Validated personal data (IBAN with mod 97, card with Luhn, national id with its checksum, e-mail, phone) is masked. While this layer is active the reply is released only after the check.
5. **Identity.** [ADR-0005](0005-customer-session-trust.md) is implemented: publishable keys only open anonymous sessions, secret keys name the customer.
6. **Logging.** Every block or flag is a `SecurityEvent` with layer, category and a masked excerpt, available at `GET /security-events`. A tenant can read and change its settings at `/settings/firewall`, including a master switch.

## Alternatives considered

- **A local classifier (Llama Prompt Guard).** Fast and free per call, but it adds a model download and framework to the image, needs a licence step and is weak on Turkish. The classifier sits behind a small protocol, so a local model can replace the small model later.
- **Presidio for personal data.** It brings spaCy models and weak Turkish entity recognition. Turkish identifiers have checksums, which validators check more precisely than a model.
- **Streaming first, filtering later.** Keeps token-by-token streaming but a leak has already left when the filter runs. The reply is held instead; the first token comes later, the total time is about the same.
- **Arbitrary tenant code as policies.** The four policy types cover one record per call. Anything beyond that (several records, business rules) would need a sandboxed rule language, which was left out on purpose.

## Consequences

- Every message costs one extra small-model call: measured on 40 harmless messages the rules take under a millisecond and the rating about 1.0 s (p95 1.5 s) and $0.0001, one second more before the first token. Documents cost one call per passage at ingestion.
- The rating sees only the current message, so an attack spread over several messages is judged message by message.
- Small sections are merged into one passage, so one poisoned sentence withholds its whole passage from retrieval.
- Rules and canary protect against known phrases and verbatim leaks; paraphrased leaks of instruction content are not detected.
- The numbers are in the README: attack success rate per category with the firewall off and on, the false-block rate on benign messages, and detection on a public prompt injection set that was never used for tuning.
