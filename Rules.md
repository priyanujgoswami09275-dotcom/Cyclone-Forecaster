# Rules.md — Guardrails for Anyone (or Any AI Tool) Working on This Repo

## Non-negotiable decisions — do not relitigate these

- Case study is **Cyclone Remal**, anchor surge value **1.2m**. Don't swap
  case studies mid-build; if a teammate wants to add another cyclone,
  that's an addition, not a replacement.
- Frontend is **Expo / React Native**. This is not a web app — don't
  reintroduce Leaflet.js or any browser-only library.
- The "AI/ML" layer is deliberately two real things, not one black box:
  a trained surge regression (genuine ML, see PRD.md) and Gemini doing
  reasoning/synthesis (genuine AI). The simulation engine (flood
  propagation, routing, shelter allocation) must be real algorithms —
  not a lookup table dressed up as a model.
- Pin the exact Gemini model string in code (`gemini-3.7-flash` as of the
  current CLAUDE.md). Don't silently swap model versions — check
  MEMORY.md for any later-verified update before changing this.

## Hard engineering rules

- **Never call Overpass, IBTrACS, or GEE live from a request handler.**
  Pre-fetch once, commit the output to `/data`, serve from static files.
  The public Overpass instance rate-limits at 2 concurrent requests/IP —
  a live call during a demo will fail.
- **Never call Gemini on slider `onChange`.** Gate every Gemini call
  behind an explicit user action ("Generate Advisory" button only) —
  free-tier rate limits will exhaust in seconds otherwise.
- **Every historical number must be traceable to a real, named source**
  (IMD, INCOIS, IBTrACS, IFRC/BDRCS, Sphere India). If a number is
  estimated rather than observed, say so explicitly in code comments and
  in any advisory copy — never present an estimate as an observed fact.
- **No auth, no location permission, no persisted history.** These are
  explicitly out of scope (see PRD.md) — don't add them "while you're in
  there."
- If reusing code drafted by another AI session, **check for stray
  `[cite: N]` fragments** left inside code blocks before running —
  they break Python/JS syntax.
- **Verify a third-party tool/service claim before adopting it** —
  don't trust another AI session's description of an MCP server, hosting
  platform, or API's pricing/availability at face value. This project has
  already caught one mischaracterized tool (a stealth/anti-bot browser
  tool described as a benign screenshot utility) and one stale pricing
  claim (a hosting platform described as "no longer free" when a new
  account's trial genuinely covers a build this short) this way.

## Session discipline (multi-tool workflow)

- Read `AGENTS.md`/`CLAUDE.md` and `MEMORY.md` before writing any code in
  a new session, regardless of which tool you're using.
- Update `MEMORY.md` before ending a session: status by module, files
  created, blockers, and a specific "Next step" for whoever picks this up
  next.
- If you disagree with a decision documented here or in CLAUDE.md, **log
  it under "Flagged for review" in MEMORY.md** — don't silently override
  it. A human resolves flagged disagreements, not the next AI session
  that happens to read them.

## Scope discipline

- Two mobile screens, total: the map screen and the advisory modal.
  Resist adding more without updating PRD.md first.
- The cut-features test: if removing a feature doesn't break the Core
  Loop (PRD.md), it's a delighter — build it last, cut it first if time
  runs short.
