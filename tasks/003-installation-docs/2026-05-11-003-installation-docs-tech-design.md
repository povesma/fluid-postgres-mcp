# 003-installation-docs: Installation Documentation Overhaul — Technical Design

**Status**: Draft
**PRD**: [2026-05-11-003-installation-docs-prd.md](./2026-05-11-003-installation-docs-prd.md)
**Created**: 2026-05-11

---

## Scope

Single-file edit of `README.md`. No code, no tests, no deps.

## Verified facts (re-checked 2026-05-11)

- `## Install` currently contains only `pip install -e .` —
  `README.md:13-19`.
- `## Use it` already uses `claude mcp add fluid-postgres-mcp -- fluid-postgres-mcp …` —
  `README.md:21-28`. The new primary example replaces both with
  `claude mcp add … uvx fluid-postgres-mcp …`.
- Package name `fluid-postgres-mcp`, entry point
  `fluid-postgres-mcp = "postgres_mcp:main"`, Python `>=3.10` —
  `pyproject.toml:2-6, 34-35`.
- `## Pre-connect scripts` (`README.md:53-143`) is the link target
  for the second primary example.

## Target README layout

```
# fluid-postgres-mcp                       (unchanged)
## Install                                 REWRITE
  mini-TOC line
  ### With Claude Code (primary)           2 snippets: inline-URL + --pre-connect-script
  ### Other AI agents                      per-client index, 8 entries
  ### Alternative install methods          pipx · pip · from-source
## Use it                                  drop the duplicate claude-mcp-add line; keep tools list
## Configure                               (unchanged)
## Pre-connect scripts                     (unchanged)
## Develop                                 NEW: clone + pip install -e ".[dev]" + pytest
## License                                 (unchanged)
```

## Per-client snippets (verified via Context7, 2026-05-11)

CLI form when the client has one, JSON otherwise. Placeholder DSN
`postgresql://reader:pw@host:5432/db` everywhere (matches
`README.md:27`).

| # | Client       | Form & key facts                                                                                  | Source                       |
|---|--------------|---------------------------------------------------------------------------------------------------|------------------------------|
| 1 | Claude Code  | CLI: `claude mcp add fluid-postgres-mcp -- uvx fluid-postgres-mcp <DSN>`                          | already in README, confirmed |
| 2 | Codex CLI    | CLI: `codex mcp add fluid-postgres-mcp --transport stdio --command "uvx fluid-postgres-mcp <DSN>"`| `/openai/codex`              |
| 3 | Cursor CLI   | CLI: `agent mcp add fluid-postgres-mcp -- uvx fluid-postgres-mcp <DSN>`                           | `/websites/cursor`           |
| 4 | Gemini CLI   | JSON `~/.gemini/settings.json` → `mcpServers.fluid-postgres-mcp.{command:"uvx",args:[…]}`         | `/google-gemini/gemini-cli`  |
| 5 | opencode     | JSON `opencode.json` → `mcp.fluid-postgres-mcp.{type:"local",command:["uvx",…]}` (array!)         | `/anomalyco/opencode`        |
| 6 | Kiro CLI     | JSON `mcp.json` → `mcpServers.fluid-postgres-mcp.{command,args}`                                  | `/websites/kiro_dev_cli`     |
| 7 | Cursor IDE   | JSON `~/.cursor/mcp.json` → `mcpServers.fluid-postgres-mcp.{command,args}`                        | `/websites/cursor`           |
| 8 | Windsurf     | JSON `~/.codeium/windsurf/mcp_config.json` → `mcpServers.fluid-postgres-mcp.{command,args}`       | `/websites/windsurf`         |
| 9 | Zed          | JSON `~/.config/zed/settings.json` → `context_servers.fluid-postgres-mcp.{command,args}` (**not** `mcpServers`) | `/zed-industries/zed`        |

Each entry ≤ 6 code lines + one caption pointing at the client's
own MCP docs.

## Gotchas to encode in the snippets

- **Zed**: key is `context_servers`, not `mcpServers`.
- **opencode**: `command` is a string **array**; entry needs
  `type: "local"`.
- **Codex/Cursor CLI**: have native `mcp add` subcommands — shorter
  than emitting JSON, prefer them.
- **No version pins** anywhere (`fluid-postgres-mcp`, not
  `fluid-postgres-mcp==0.1.0`) so routine releases don't touch the
  README.

## Verification

| Check | How |
|-------|-----|
| Primary path works end-to-end | Manual smoke: copy-paste primary snippet against a local Postgres, confirm `claude mcp` shows the server and `status` responds. |
| Structure (FR-1…FR-6, NFR-1) | Read the rendered README on GitHub: 2 primary snippets, 9 per-client entries, alternative-methods section below them, `## Develop` at the bottom. |
| No version pin | `grep -E "fluid-postgres-mcp(==\|@)[0-9]" README.md` returns empty. |
| Each per-client entry ≤ 6 code lines | Visual check during PR review. |

## Rejected alternatives

- **Full per-client walkthroughs** — violates the brevity
  constraint and creates 8x maintenance load. Rejected per user's
  PRD answer.
- **`pipx install` as the primary path** — adds a pre-step before
  any snippet works; `uvx` keeps the zero-install property.
  `pipx` lives in *Alternative install methods*.
- **Separate `INSTALL.md` / `CONTRIBUTING.md`** — overkill; the
  `## Develop` README section is enough.

## Rollback

`git revert` on the README commit.

---

**Next**: `/dev:tasks` for the breakdown (expect ~5 subtasks).
