# Domain docs

## Layout and reading rules

This is a single-context repository: `CONTEXT.md` at the root and ADRs under `docs/adr/`.
Before exploring, read `CONTEXT.md` and ADRs relevant to the work.
If a root `CONTEXT-MAP.md` is introduced later, follow its pointers to relevant context documents and context-specific ADRs.

If these documents do not exist, proceed silently. Do not propose scaffolding them upfront.
The domain-modeling skill creates them lazily when terms or decisions are resolved, including through grill-with-docs and improve-codebase-architecture.

## Vocabulary and decisions

Use the glossary's terms consistently in discussion, tickets, code, and tests.
For missing concepts, reconsider invented terminology or note the gap for domain-modeling.
Explicitly flag conflicts with existing ADRs rather than silently overriding them.
