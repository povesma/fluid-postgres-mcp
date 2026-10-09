# 003-installation-docs - Task List

## Relevant Files
- [2026-05-11-003-installation-docs-tech-design.md](./2026-05-11-003-installation-docs-tech-design.md) :: 003-installation-docs - Technical Design
- [2026-05-11-003-installation-docs-prd.md](./2026-05-11-003-installation-docs-prd.md) :: 003-installation-docs - Product Requirements Document
- [../../README.md](../../README.md) :: The only file modified by this task.
- [../../pyproject.toml](../../pyproject.toml) :: Source of truth for package name (`fluid-postgres-mcp`), entry point, and Python `>=3.10` requirement — referenced, not modified.

## Notes
- Single-file change: `README.md`. No code, no tests, no deps.
- Per-client snippet syntaxes are pre-verified in the tech design
  (`§ Per-client snippets`). Use them verbatim.
- No version pin in any install snippet (`fluid-postgres-mcp`, not
  `fluid-postgres-mcp==0.1.0`) so routine releases don't touch the
  README.
- Placeholder DSN is `postgresql://reader:pw@host:5432/db`
  everywhere, matching the existing `README.md:27`.

## Tasks

- [X] 1.0 **User Story:** As an AI-agent operator, I want a
  `uvx`-driven primary install snippet at the top of the README
  so I can register `fluid-postgres-mcp` with Claude Code in one
  copy-paste without cloning or installing anything globally.
  - [X] 1.1 Replace `README.md:13-19` (`## Install` block) with a
    new `## Install` heading plus a `### With Claude Code
    (primary)` subsection. [verify: code-only]
  - [X] 1.2 Under the primary subsection, add the inline-URL
    example: `claude mcp add fluid-postgres-mcp -- uvx
    fluid-postgres-mcp postgresql://reader:pw@host:5432/db`.
    [verify: code-only]
  - [X] 1.3 Add the second primary example using
    `--pre-connect-script /path/to/your-tunnel.sh`, with a
    one-line caption containing a markdown link to the
    `## Pre-connect scripts` section anchor. [verify: code-only]
  - [X] 1.4 Resolution (a): dropped the `claude mcp add` example
    from the former `## Use it`; renamed the section to
    `## How to use Fluid Postgres MCP`; kept the tools list and
    `execute_sql` paragraph intact. [verify: code-only]
  - [X] 1.5 Insert the mini-TOC line immediately under
    `## Install`, linking to *Other AI agents*, *Alternative
    install methods*, and *Develop*. [verify: code-only]

- [X] 2.0 **User Story:** As an operator of a non-Claude-Code MCP
  client (Codex CLI, Cursor CLI/IDE, Gemini CLI, opencode, Kiro
  CLI, Windsurf, Zed), I want a short per-client snippet in an
  index so I can register the same server with my own agent
  without leaving the README.
  - [X] 2.1 Add `### Other AI agents` subsection under
    `## Install`. [verify: code-only]
  - [X] 2.2 Add Codex CLI entry. [verify: code-only]
  - [X] 2.3 Add Cursor CLI entry. [verify: code-only]
  - [X] 2.4 Add Gemini CLI entry. [verify: code-only]
  - [X] 2.5 Add opencode entry. [verify: code-only]
  - [X] 2.6 Add Kiro CLI entry. [verify: code-only]
  - [X] 2.7 Add Cursor IDE entry. [verify: code-only]
  - [X] 2.8 Add Windsurf entry. [verify: code-only]
  - [X] 2.9 Add Zed entry (uses `context_servers`, not
    `mcpServers`). [verify: code-only]
  - [X] 2.10 Each per-client entry ≤ 6 code lines (CLI entries
    4 lines, JSON entries 6 lines). [verify: code-only]

- [X] 3.0 **User Story:** As a user who prefers a persistent
  install over `uvx`, I want `pipx`, `pip`, and from-source
  options listed below the primary path so I can pick what fits
  my environment.
  - [X] 3.1 Add `### Alternative install methods` subsection
    under `## Install`, positioned after *Other AI agents*.
    [verify: code-only]
  - [X] 3.2 Add `pipx install fluid-postgres-mcp` one-liner with
    a one-line caption. [verify: code-only]
  - [X] 3.3 Add `pip install fluid-postgres-mcp` one-liner with a
    one-line caption about isolated environments.
    [verify: code-only]
  - [X] 3.4 Add from-source one-liner: `git clone … && pip
    install .` with a one-line caption. [verify: code-only]

- [X] 4.0 **User Story:** As a contributor, I want the editable
  `pip install -e .` instruction in a developer-oriented section
  so working from a clone is still documented but not in the
  user-install path. (reopened — added 4.4 release-process docs)
  - [X] 4.1 Add `## Develop` section between `## Pre-connect
    scripts` and `## License`. [verify: code-only]
  - [X] 4.2 Add clone + `pip install -e ".[dev]"` + `pytest`
    snippet, plus a one-line pointer to `ARCHITECTURE.md` and
    `TESTING-METHODOLOGY.md`. [verify: code-only]
  - [X] 4.3 Confirm `pip install -e .` no longer appears anywhere
    inside `## Install`.
    → grep on the `## Install`-to-`## How to use` slice returned 0
      matches for `pip install -e`. [live] (2026-05-11)
    [verify: code-only]
  - [X] 4.4 Add a `### Release` block under `## Develop`
    documenting the release flow used for v0.1.1: bump version
    in `pyproject.toml`, commit, tag, clean `dist/`, build via
    `uvx --from build pyproject-build`, `twine check`, push
    commit + tag, then upload via `twine` with `PYPI_TOKEN`
    sourced from `.env` (`set -a; source .env; set +a`) so the
    token never enters the agent's context. Capture this so
    future releases don't rediscover the venv/build-deps
    paper-cut. [verify: code-only]

- [X] 5.0 **User Story:** As a maintainer, I want a one-off smoke
  check before merge so I'm confident the published primary
  snippet actually resolves, spawns, and registers.
  - [X] 5.1 Confirm `grep -E "fluid-postgres-mcp(==\|@)[0-9]"
    README.md` returns empty (no version pins).
    → `grep -nE "fluid-postgres-mcp(==|@)[0-9]" README.md` → exit 1,
      zero matches. [live] (2026-05-11)
    [verify: code-only]
  - [X] 5.2 Run the primary snippet against a local Postgres
    (`claude mcp add fluid-postgres-mcp -- uvx fluid-postgres-mcp
    postgresql://reader:pw@127.0.0.1:5432/postgres`) and confirm
    `claude mcp` lists the server and the `status` tool
    responds.
    → User confirmed via `claude mcp list`: `fluid-postgres-mcp:
      uvx fluid-postgres-mcp postgresql://postgres:pw@127.0.0.1:5432/postgres
      - ✓ Connected`. [live] (2026-05-11)
    [verify: manual-run-user]

- [X] 6.0 **User Story:** As an operator installing with
  `uvx fluid-postgres-mcp`, I want a fresh install to start so that
  the documented snippets work again after `mcp` 2.x was published
  (hotfix 0.1.4, found 2026-10-09: published 0.1.3 fails at import
  with `No module named 'mcp.server.fastmcp'`, because
  `pyproject.toml:8` allows any `mcp[cli]>=1.25.0`) [6/0]
  - [X] 6.1 Add a unit test (e.g. `tests/unit/test_dependencies.py`)
    that reads `pyproject.toml` and asserts the `mcp` requirement
    has an upper bound `<2`. Test fails. [verify: auto-test]
    → red as expected: requirement read as `mcp[cli]>=1.25.0`; run
      with `--noconftest` because conftest import fails on mcp 2.x
      [live] (2026-10-09)
  - [X] 6.2 Change `pyproject.toml:8` to `"mcp[cli]>=1.25.0,<2"`;
    reinstall `.venv` (`uv pip install -e ".[dev]"`) so it resolves
    `mcp` 1.x; 6.1 passes and the full unit suite is green.
    [verify: auto-test]
    → mcp 1.30.0 resolved; unit suite 247 passed, 1 xfailed (incl.
      pin test); entry point `--version` starts [live] (2026-10-09)
  - [X] 6.3 Add `## [0.1.4] - <date>` to `CHANGELOG.md` (Fixed: fresh
    installs failed at startup with `mcp` 2.x; `mcp` is now pinned
    below 2) and bump `pyproject.toml` to `0.1.4`. Commit on `main`
    (no AI attribution lines): first any pending docs-only changes
    as their own commit, then the pin + test, then the bump +
    CHANGELOG. Confirm `git diff --quiet` and
    `git diff --staged --quiet` both succeed. [verify: code-only]
  - [X] 6.4 Write the hand-written GitHub Release body per the
    README rule to a scratch file; show it and get the user's
    explicit approval to release 0.1.4 (PyPI upload cannot be
    undone). [verify: manual-run-user]
    → user approved the body and the release [live] (2026-10-09)
  - [X] 6.5 Precondition: approval from 6.4 recorded in this
    session; otherwise stop. Run `scripts/release.sh --version
    0.1.4 --release-body-file <file> --yes` (pushes and uploads),
    then `scripts/release-check.sh 0.1.4`. [verify: manual-run-claude]
    → script pushed main + tag, then failed at step 5 (gh token had
      no push access); finished by hand per README steps 5–7 after
      the user switched gh account; release-check: fully released
      [live] (2026-10-09)
  - [X] 6.6 Smoke the published package from a clean cache:
    `uvx --refresh fluid-postgres-mcp@0.1.4 --version` prints
    `fluid-postgres-mcp 0.1.4`. [verify: manual-run-claude]
    → prints `fluid-postgres-mcp 0.1.4` [live] (2026-10-09)

- [ ] 7.0 **User Story:** As the maintainer, I want the release
  tooling to report and roll back a partial release correctly so
  that a failure after the push never leaves a stray tag (found
  during the 0.1.4 release) [4/0]
  - [X] 7.1 `scripts/release.sh`: on a failure after step 4 and
    before PyPI, also delete the pushed remote tag (or stop and
    print the exact recovery commands), not only the local tag.
    Today the rollback leaves `v<X.Y.Z>` on origin.
    [verify: manual-run-claude]
    → chose "keep tag + print recovery commands" (no automatic
      deletion of a published tag); in a throwaway clone with a local
      bare origin: step-5 failure kept the tag locally and on origin
      and printed the commands; step-3 failure deleted the local tag
      [live] (2026-10-09)
  - [X] 7.2 `scripts/release-check.sh`: check the tag on origin
    (`git ls-remote --tags origin`), not only the local tag; today
    it reports MISSING while the remote tag exists.
    [verify: manual-run-claude]
    → origin tag decides, local shown as informational; a draft
      Release now fails the check; real 0.1.4 → OK; clone with the
      tag only on origin → origin found, FAIL on the other sources
      [live] (2026-10-09)
  - [~] 7.3 `scripts/release.sh` preflight: before tagging, check
    that `.venv/bin/twine` exists, `.env` exists, and the gh token
    has push access (`gh api repos/<owner>/<repo> --jq
    .permissions.push`), so these fail before anything is pushed.
    [verify: manual-run-claude]
    → negative case live: gh token without push access stopped the
      script at step 0, before tag/push (2026-10-09); positive case
      pending a gh account with push access
  - [X] 7.4 `scripts/release.sh` and `scripts/release-check.sh`
    follow the user's "no silent waits" rule: every step logs one
    line with elapsed time (no step output sent to `/dev/null`
    without a log line), and every network call (`git push`, `gh`,
    `curl`, `twine`, `uvx`) has a time limit, so a stuck release is
    visible at once. [verify: manual-run-claude]
    → `with_timeout` helper (no `timeout` on macOS); log lines carry
      elapsed seconds; unreachable origin → `git ls-remote` stopped
      at 30s, check finished in 32s; real 0.1.4 check OK; README
      release section updated [live] (2026-10-09)
