# MCP server

`xpt mcp` serves xpt's review commands as [MCP](https://modelcontextprotocol.io) tools over stdio. An MCP client lists the tools with their argument schemas and calls them directly, instead of running `xpt` in a shell and parsing its output.

| Tool | Command | Arguments |
|---|---|---|
| `xpt_failures` | `xpt failures` | `run` |
| `xpt_show` | `xpt show` | `id` (required), `run`, `full` |
| `xpt_accept` | `xpt accept` | `ids`, `all`, `missing`, `dry_run`, `force`, `verify`, `run` |
| `xpt_reject` | `xpt reject` | `ids` (required), `run` |
| `xpt_runs` | `xpt runs` | none |
| `xpt_flaky` | `xpt flaky` | none |

Each tool returns the object its command prints with `--json`, described in [JSON output](json.md). `xpt_accept` applies the same checks as `xpt accept`: it refuses stale, suspect, and flaky values unless `force` is true, and needs one of `ids`, `all`, or `missing`. It does not ask for its own confirmation, so leave your client's approval prompt on for it if you want to review each accept.

The server reads and writes the project in the directory it starts in. Pass `--root PATH` for another one. `xpt` must be on the client's `PATH`, or the configuration must name the command that runs it, such as `uv run xpt`.

## Claude Code

`xpt init --mcp` adds the server to `.mcp.json` in the project root, which Claude Code reads for project-scoped servers:

```json
{
  "mcpServers": {
    "xpt": {"command": "xpt", "args": ["mcp"]}
  }
}
```

The same entry with the Claude Code CLI:

```sh
claude mcp add --scope project xpt -- xpt mcp
```

In a uv project where xpt is a dev dependency, run it through uv:

```json
{
  "mcpServers": {
    "xpt": {"command": "uv", "args": ["run", "xpt", "mcp"]}
  }
}
```

## Codex

Add the server to `~/.codex/config.toml`:

```toml
[mcp_servers.xpt]
command = "xpt"
args = ["mcp"]
```

## Other clients

Any client that starts stdio servers takes the same command, `xpt mcp`. The server speaks MCP protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`, and answers a client that asks for another version with `2025-11-25`. It writes only protocol messages to stdout and logs to stderr.
