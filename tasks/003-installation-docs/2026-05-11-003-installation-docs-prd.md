# 003-installation-docs: Installation Documentation Overhaul — PRD

**Status**: Draft
**Created**: 2026-05-11
**Author**: Claude (via dev workflow)

---

## Context

`fluid-postgres-mcp` is now published on production PyPI as version
`0.1.0` with an installable console entry point `fluid-postgres-mcp`
— verified via: claude-mem observation #13387 (PyPI publish) and
#13372 (entry point verification), 2026-05-10. Despite this, the
README's only install instruction is:

```
pip install -e .
```

— verified via: `README.md:13-19`, 2026-05-11.

This is an *editable* install from a working tree, which requires
the user to first clone the repository — a needless friction step
now that the package resolves on the public index. It also presents
the slowest, most invasive install path as the headline option,
even though most users (AI-agent operators wiring an MCP into
Claude Code, Codex, Gemini CLI, opencode, Kiro, or an editor host)
want a single copy-pasteable command that registers the server
with their client and never touches their global Python.

This PRD reframes README installation around the agent-operator
use case: a primary command that uses `uvx` for zero-install
execution and registers with the user's MCP client, with an index
covering the most common AI agents and a short list of alternative
install methods below for users who want a persistent install or
are working from source.

### Current State (observed)

- README's `## Install` section contains only `pip install -e .`
  and the Python 3.10+ / entry-point note — verified via:
  `README.md:13-19`, 2026-05-11.
- The README's `## Use it` section already shows
  `claude mcp add fluid-postgres-mcp -- fluid-postgres-mcp
  postgresql://reader:pw@host:5432/db`, which assumes the binary
  is already on `$PATH` — verified via: `README.md:21-28`,
  2026-05-11.
- Package name on PyPI is `fluid-postgres-mcp`, version `0.1.0`,
  console script `fluid-postgres-mcp = "postgres_mcp:main"` —
  verified via: `pyproject.toml:2-3, 34-35`, 2026-05-11.
- Minimum supported Python is `>=3.10` — verified via:
  `pyproject.toml:6`, 2026-05-11.
- Package depends on `mcp[cli]`, `psycopg[binary]`, `pglast`, and
  five other runtime libraries — verified via: `pyproject.toml:7-15`,
  2026-05-11.
- Pre-connect scripts (run-and-exit *and* long-running tunnel mode)
  are the supported mechanism for non-trivial connection setup
  (e.g. SSM port-forwarding, credential rotation) — verified via:
  `README.md:53-118`, 2026-05-11.
- Installation guidance for any AI client other than Claude Code
  is absent from the README — [assumption, verify in tech-design].

### Past Similar Work (from claude-mem)

- PyPI publishing readiness was tracked in session S5097
  (2026-05-10) and the package was published in observation
  #13387 with metadata verified in #13372 / #13390 — these
  established that `uvx fluid-postgres-mcp` and
  `pipx install fluid-postgres-mcp` resolve against the public
  index.

## Problem Statement

**Who**: Operators of AI-agent CLIs (Claude Code, Codex, Gemini
CLI, opencode, Kiro) and editor MCP hosts (Cursor, Windsurf,
Zed) who want to add `fluid-postgres-mcp` to their agent.

**What**: The README's headline install step requires cloning the
repo and running an editable pip install, which is the slowest,
most invasive option and is wrong for non-contributors. There is
also no per-client registration guidance for any agent other than
Claude Code.

**Why**: First-time users will either follow the editable-install
path (wasting time, polluting a Python environment) or bounce to
each agent's own docs to figure out the registration command for
an MCP server they just discovered. Either outcome reduces
adoption of a server that is otherwise ready for use.

**When**: At first encounter with the README, before the user
decides whether to try the server at all.

## Goals

### Primary Goal

A reader landing on the README can copy one command, paste it
into their AI agent's terminal, and have a working MCP server
registered — without cloning the repo, without choosing a Python
environment, and without leaving the README.

### Secondary Goals

- Make the alternative install methods (pipx, pip, from-source)
  discoverable but visually subordinate to the primary path.
- Provide a per-AI-client index so users of non-Claude-Code agents
  don't have to translate the example themselves.
- Preserve the editable install for contributors, in a place where
  contributors will look.

## User Stories

### Epic

As an AI-agent operator, I want to install `fluid-postgres-mcp`
into my agent with a single copy-pasteable command, so that I
can start querying Postgres through the agent without setting up
a Python environment or cloning the repo.

### User Stories

1. **As an** AI-agent operator using Claude Code
   **I want** a single copy-pasteable command at the top of the
   README that registers `fluid-postgres-mcp` with my Claude Code
   installation
   **So that** I can get the MCP working in under a minute without
   reading further.

   **Acceptance Criteria**:
   - [ ] README shows two `claude mcp add ... uvx fluid-postgres-mcp ...`
         command examples above any other install instruction.
   - [ ] One example passes a direct `postgresql://...` URL inline
         (for users without a tunnel script).
   - [ ] Second example uses a `--pre-connect-script` and contains
         a link to the README's pre-connect-script section for the
         script protocol requirements.
   - [ ] Neither example requires cloning the repo.

2. **As an** operator of a non-Claude-Code AI agent (Codex CLI,
   Gemini CLI, opencode, Kiro CLI) or editor host (Cursor, Windsurf,
   Zed)
   **I want** a short per-client snippet in an index
   **So that** I can register the same `uvx fluid-postgres-mcp`
   command with my own agent without leaving the README.

   **Acceptance Criteria**:
   - [ ] README contains an index covering at least: Claude Code
         (first), Codex CLI, Gemini CLI, opencode, Kiro CLI,
         Cursor, Windsurf, Zed.
   - [ ] Each entry is brief: one CLI command *or* one config-file
         JSON snippet, plus a one-line caption.
   - [ ] No per-client entry exceeds ~6 lines of code.
   - [ ] Entries explicitly note that the canonical reference is
         the agent's own MCP docs.

3. **As a** user who prefers a persistent install over `uvx`
   **I want** alternative install methods listed below the primary
   path
   **So that** I can choose `pipx`, `pip`, or a from-source install
   if it fits my environment better.

   **Acceptance Criteria**:
   - [ ] README contains an "Alternative install methods" section
         covering at least `pipx install`, `pip install`, and a
         manual from-source path.
   - [ ] Each alternative method is given one short snippet, not a
         multi-step walkthrough.
   - [ ] The section is visually subordinate to the primary path
         (lower on the page, or behind a "Details" / collapsible
         block, or under a `###` heading).

4. **As a** contributor
   **I want** the editable `pip install -e .` instruction to remain
   discoverable in a developer-oriented section
   **So that** working from a clone is still documented.

   **Acceptance Criteria**:
   - [ ] `pip install -e .` is moved out of the headline install
         section.
   - [ ] It appears in a "Develop / Contribute" (or similarly named)
         section.
   - [ ] That section mentions installing dev extras
         (`pip install -e ".[dev]"` or equivalent).

5. **As a** maintainer
   **I want** the install docs to stay grounded in the actual
   published package
   **So that** instructions don't drift from the live PyPI
   metadata.

   **Acceptance Criteria**:
   - [ ] All install commands reference the exact PyPI package name
         (`fluid-postgres-mcp`) and the exact console entry point
         (`fluid-postgres-mcp`).
   - [ ] Python version requirement matches `pyproject.toml`
         `requires-python`.
   - [ ] Instructions do not pin a specific package version unless
         explicitly required (so the README does not need updating
         on every release).

## Requirements

### Functional Requirements

1. **FR-1**: Replace the README's `## Install` section so that the
   first install instruction shown is a zero-install command
   suitable for an AI-agent operator (using `uvx`).
   - **Priority**: High
   - **Rationale**: Primary user persona is the agent operator.
   - **Dependencies**: PyPI publish (#13387) — already done.

2. **FR-2**: Provide two primary install examples: one with an
   inline `postgresql://...` URL, one with `--pre-connect-script`
   and a link to the pre-connect-script section.
   - **Priority**: High
   - **Rationale**: Tunnel-script users (the main reason this fork
     exists) should not be steered toward the inline-URL form.

3. **FR-3**: Add a per-AI-client install index covering Claude
   Code (first), Codex CLI, Gemini CLI, opencode, Kiro CLI,
   Cursor, Windsurf, Zed. Each entry is one snippet plus a
   one-line caption; entries defer detail to each agent's own
   docs.
   - **Priority**: High
   - **Rationale**: Non-Claude users currently get no guidance.

4. **FR-4**: Add an "Alternative install methods" section
   covering `pipx install`, `pip install` (non-editable), and
   manual from-source. Subordinate to FR-1 in the page layout.
   - **Priority**: Medium
   - **Rationale**: Persistence-preferring users still need a path.

5. **FR-5**: Move `pip install -e .` to a "Develop / Contribute"
   section that also mentions dev extras.
   - **Priority**: Medium
   - **Rationale**: Editable install is a contributor concern.

6. **FR-6**: Cross-link install snippets that involve a tunnel
   script to the existing `## Pre-connect scripts` section, so
   users do not paste a broken setup.
   - **Priority**: Medium
   - **Rationale**: Tunnel-script setup has non-obvious protocol
     requirements documented elsewhere in the README.

### Non-Functional Requirements

1. **NFR-1**: Brevity — the entire install + per-client index
   section should fit in one screen on a typical 1080p browser
   window, or be clearly skim-able if it doesn't. No per-client
   entry exceeds ~6 lines.

2. **NFR-2**: Accuracy — every command must be runnable as
   written against the published `0.1.0` package, with no
   placeholder package names or undefined env vars.

3. **NFR-3**: Maintenance — instructions must not embed the
   package version, so a routine PyPI release does not require
   README edits.

4. **NFR-4**: Discoverability — the per-client index must be
   reachable from the README table of contents or top-level
   headings (not buried inside another section).

### Technical Constraints

- Must match published PyPI metadata: package name
  `fluid-postgres-mcp`, entry point `fluid-postgres-mcp`, Python
  `>=3.10`. Verified via `pyproject.toml:2-6, 34-35`, 2026-05-11.
- Must not invent flags or env vars that don't exist in the
  current CLI; the existing `Configure` table in the README is
  the source of truth — verified via: `README.md:37-51`,
  2026-05-11.
- Each per-client snippet should be verified once against that
  client's current MCP-registration syntax; concrete syntax for
  each agent lives in tech-design, not here.
  [assumption, verify in tech-design]

## Out of Scope

- Writing a separate INSTALL.md file. All changes land in the
  existing README (and possibly a CONTRIBUTING.md if one is
  introduced for the editable install section).
- Producing official Docker images, Homebrew formulae, or any
  packaging beyond what is already on PyPI.
- Detailed troubleshooting guides for each AI client. The index
  defers to each agent's own MCP docs.
- Screenshots, GIFs, or animated walkthroughs.
- Restructuring or rewriting other README sections
  (`## Use it`, `## Configure`, `## Pre-connect scripts`,
  `## License`). Only cross-links into those sections may be
  added.

## Success Metrics

1. **Time-to-first-MCP-call** for a Claude Code user landing on
   the README: one copy-paste and one restart of the agent.
   Target: no pre-install step (no `pip`, no `pipx`, no clone)
   required.

2. **Per-client coverage**: at least 8 AI clients listed in the
   install index (Claude Code + 7 others), each with a working
   one-snippet entry. Target: 8 / 8.

3. **README diff scope**: the only README sections substantively
   modified are `## Install` (replaced) and a new install-index
   section. `## Use it`, `## Configure`, `## Pre-connect scripts`,
   and `## License` remain untouched except for incoming
   cross-links. Target: 0 unrelated changes.

4. **Accuracy regression**: zero commands in the new install
   section reference a package name, entry point, or Python
   version that doesn't match `pyproject.toml`. Verified by a
   spot-check before merging.

## References

### From Codebase
- `README.md:13-19` — current `## Install` section to be replaced.
- `README.md:21-35` — `## Use it` example, depends on the binary
  being on `$PATH`; will be cross-linked from the new install
  section.
- `README.md:53-143` — `## Pre-connect scripts` section; new
  primary install example with `--pre-connect-script` links here.
- `pyproject.toml:2-6, 34-35` — package name, version, Python
  requirement, console entry point.

### From History (Claude-Mem)
- #13387 (2026-05-11) — `fluid-postgres-mcp 0.1.0` published to
  production PyPI with anonymised metadata.
- #13372 (2026-05-11) — package metadata and CLI entry point
  verified post-publish.
- #13390 (2026-05-11) — anonymised metadata confirmed live on
  production PyPI.

---

**Next Steps**:
1. Review and refine this PRD.
2. Run `/dev:tech-design` to create technical design (per-client
   exact commands, README diff plan).
3. Run `/dev:tasks` to break down into tasks.
