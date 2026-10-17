# HiBot tools as an MCP server

`python -m hibot.mcp_server` exposes the HiBot investigation tools (see `hibot/tools/`) over MCP stdio, so Claude Code
(or any MCP client) can use the same tools as HiBot's own loop: `code_search`, `read_file`, `sql`, `run_template`,
`list_templates`, `graph_*`, `column_writers`, `similar_tickets`, `ticket`, `ticket_window`, `cards_*`, `git_log`, `git_blame`.

The server is stdlib only (newline-delimited JSON-RPC 2.0: initialize, ping, tools/list, tools/call). It loads the
git-ignored `.env` through `hibot.envfile` (only the number of variables is logged to stderr, values are never printed).
Without `HIBOT_DB_*` the SQL tools return an error and everything else keeps working.

## Register in Claude Code

Project-scoped `.mcp.json` (put it in the repo you work from; this repo does not ship one):

```json
{
  "mcpServers": {
    "hibot": {
      "command": "python",
      "args": ["-m", "hibot.mcp_server"],
      "cwd": "d:\\repos\\HiBot"
    }
  }
}
```

or with the CLI: `claude mcp add hibot --scope user -- python -m hibot.mcp_server` (run it from `d:\repos\HiBot`,
or prefer the JSON form with `cwd`).

## Safety

* Read-only: SQL goes through `hibot.db` (validator, PII/credential column refusal, TOP limit, masking, audit log).
  The only write is the SQL audit log.
* Secret files (`appsettings*.json`, `.env*`, `translate.js`, `web.config`, keys/certs) are never searched or read;
  node_modules/bin/obj/build/dist/.git are skipped; credentials in code lines are masked.
* Every result is capped (about 8 KB; read_file 16 KB; templates/tickets 12 KB) and carries `truncated`.
* git: author names reduced to initials, no e-mails; only repos that are git clones (frontends, SiriusApi).
* `code_search` uses ripgrep when `rg` is on PATH (faster), otherwise a threaded Python scan.

## From Python / the ArGPT loop

```python
from hibot.tools import call_tool, tool_catalog
print(tool_catalog())                       # compact prompt text; the model answers {"tool": ..., "args": {...}}
call_tool("code_search", {"pattern": "repairDateEditUserTypeIds", "glob": "*.js"})
```
