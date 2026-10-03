# MCP registry

MCP servers are treated as untrusted external inputs. Source of the inventory: the agent CLI's `mcp list` during the
2026-10-02 bootstrap (cached by `vnxdna doctor` in `/opt/vnx-dna/run/mcp-health.json`). All current servers are
account-level connectors of the owner's agent-CLI login, not installed on this VPS; none is configured in a project
`.mcp.json`.

**Policy:** `config/laya/tools.yaml` → `mcp.allowed_servers: []`. Harness agent runs get no MCP server. VNX-DNA does not
depend on any MCP server; a missing or failing server only produces a doctor WARN. GitHub access is through the `gh`
CLI, used by humans only (agents never push).

**Decision:** no new MCP servers were installed. Nothing in the VNX-DNA workflow needs one (filesystem, git, tests and
docs are local tools), and a third-party server is code with access to data.

| Server | Purpose | Auth | Data accessed | Write | Risk | Failure behaviour | VNX-DNA agents |
|---|---|---|---|---|---|---|---|
| account connector: Docs | living documents in the owner's agent account | owner's agent account (OAuth) | docs the owner creates | yes (create/edit docs) | MEDIUM — publishes content | doc operations fail; no VNX-DNA effect | not allowed |
| account connector: Gmail | owner's mailbox | Google OAuth | all mail | yes (send, draft, label, trash) | **HIGH** — external send, personal data | mail actions fail | **never** |
| account connector: Google Drive | owner's Drive files | Google OAuth | all Drive files | yes (create, update, share, trash) | **HIGH** — sharing/exfiltration | file actions fail | **never** |
| account connector: Notion | Notion workspace | Notion OAuth — **needs authentication** (not connected) | workspace pages | yes once connected | HIGH once connected | currently unavailable | never |
| composio | gateway to many third-party apps and a remote bash/workbench | Composio account | whatever apps are connected there | yes, broad | **HIGH** — broad third-party and remote execution | tool calls fail | **never** |

## Adding a server (checklist)

1. Need: which VNX-DNA task requires it and why a local tool cannot do it.
2. Source: official publisher, pinned version, code inspected; no `npx`/`uvx` of an unpinned package.
3. Least privilege: read-only scopes where possible; a token used only for this server, stored outside Git.
4. Data: list exactly what it can read and write; never mount `~/.ssh`, secrets directories or `.env` files.
5. Record a row above (name, purpose, permissions, auth, data, write, risk, failure behaviour).
6. Add it to `mcp.allowed_servers` only for the roles that need it, and record the decision in the commit.
7. Verify `vnxdna doctor` and an agent dry run (`vnxdna laya plan …`).
