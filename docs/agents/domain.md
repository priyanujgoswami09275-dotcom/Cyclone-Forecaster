# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

Layout: **single-context** (decided 2026-09-28). No monorepo signals are
present — no `pnpm-workspace.yaml`, no `workspaces` field, no `packages/` —
so there is one `CONTEXT.md` and one `docs/adr/` at the repo root.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/adr/`**: read ADRs that touch the area you're about to work in

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

### Where this repo keeps its knowledge today

Neither `CONTEXT.md` nor `docs/adr/` exists yet, so the rules above resolve to
"proceed silently" — which would mean these skills read none of the project's
recorded decisions. The equivalent material is already written, just under
different names, and a `CONTEXT.md` that indexes it is cheaper than moving it:

- **`CLAUDE.md`** — what the project is, module assignments, tech stack per layer, the data sources, and the corrections/gotchas list. Start here.
- **`MEMORY.md`** — the decision and status log: "Flagged for review" is the ADR queue, "Session log" is the history, "Status by module" is the current state.
- **`Architecture.md`**, **`Design.md`**, **`PRD.md`**, **`Rules.md`**, **`GEMINI.md`**, **`AGENTS.md`** — the standing documents, per their own scope.

## File structure

```
/
├── CONTEXT.md
├── CLAUDE.md
├── MEMORY.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
└── backend/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
