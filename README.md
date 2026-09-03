# route-audit

Static routing-graph parser and linter for LLM route configs.

A multi-provider routing config is a graph: entry points, fallback edges, aliases,
priorities. Most of the ways it goes wrong — a fallback loop, an alias pointing at a
model you deleted, two routes claiming the same priority, a chain with no way to
terminate — are decidable from the document alone. route-audit decides them.

route-audit **validates architecture and does not send or proxy prompts**. It opens no
sockets, holds no credentials, knows no provider catalogue, and never checks live
availability. It reads one YAML file and prints diagnostics.

## Install

```bash
pip install -e ".[dev]"   # or: make install
```

Requires Python 3.11+. The only runtime dependency is PyYAML.

## Usage

```bash
route-audit lint routes.yml
```

A clean graph prints nothing and exits `0`:

```bash
$ route-audit lint examples/routes.yml
$ echo $?
0
```

A graph with problems prints one finding per line, sorted by `(code, path, message)`
so output is stable enough to diff or grep:

```bash
$ route-audit lint broken.yml
cycle routes.chat: fallback edges form a cycle among nodes: backup, primary
dangling_alias aliases.fast: alias 'fast' points at 'chat.missing', which is not a known route or node
duplicate_priority routes: priority 10 is shared by routes: chat, embed
unreachable_node routes.chat.nodes.orphan: node 'orphan' is not the entry node 'primary' and is not reachable from it via fallbacks
```

For machine consumption, `--json` (or `--format json`):

```bash
$ route-audit lint examples/routes.yml --json
{
  "file": "examples/routes.yml",
  "ok": true,
  "exit_code": 0,
  "findings": []
}
```

Each finding in `findings` is a `{"code", "path", "message"}` object, in the same
order as the text output.

### Flags

| Flag | Meaning |
| --- | --- |
| `--format {text,json}` | Output format. Default `text`, one finding per line. |
| `--json` | Shorthand for `--format json`. |
| `--version` | Print the version and exit. |

## Schema

The document is a mapping with a required `routes` list and an optional top-level
`aliases` block. Unknown keys are ignored, so the schema stays forward-compatible with
whatever your gateway also reads out of the same file.

```yaml
routes:
  - id: chat                     # required, unique across routes
    alias: default-chat          # optional; `aliases: [a, b]` for several
    priority: 10                 # optional integer; must be unique across routes
    capabilities: [chat, tools]  # optional list of strings
    context_limit: 128000        # optional integer
    entry: primary               # optional node id; defaults to the first node
    nodes:                       # required, non-empty
      - id: primary              # required, unique within the route
        provider: openai         # required non-empty string
        model: gpt-4o            # required non-empty string
        terminal: true           # optional bool; defaults to "has no outgoing fallback"
        capabilities: [chat, tools]
        context_limit: 128000
    fallbacks:                   # optional directed edges between nodes in this route
      - from: primary            # required node id or alias
        to: backup               # required node id or alias
        on: timeout              # optional label, not interpreted

aliases:                         # optional; names bound to `route` or `route.node`
  fast: chat.backup
```

Field notes:

- **`routes`** — the top-level list of entry points. Each route owns its own `nodes`
  and `fallbacks`; edges never cross route boundaries.
- **`provider` / `model`** — an opaque pair identifying where a request would go.
  route-audit never validates these against a real catalogue.
- **`alias`** — a second name for a route or node. Declared inline on a route or node
  (always resolvable), or in the top-level `aliases:` block (where it can dangle).
  Alias names must be unique across the document.
- **`fallbacks`** — `from`/`to` pairs forming the fallback graph inside one route.
  Endpoints may use node ids or aliases, and must resolve to a node in the same route.
- **`capabilities`** and **`context_limit`** — declarative metadata carried on routes
  and nodes. Parsed and type-checked, not otherwise interpreted.
- **`priority`** — integer ordering hint across routes. Two routes sharing one is a
  finding, because the tie-break would be undefined.
- **`terminal`** — marks a node that can end a fallback chain. When unset, a node with
  a model and no outgoing fallback edge counts as terminal.

A complete, lint-clean example lives in [`examples/routes.yml`](examples/routes.yml).

## Diagnostics

Every code route-audit can emit. Codes, paths, message text, and ordering are part of
the public contract.

| Code | Meaning |
| --- | --- |
| `cycle` | Fallback edges in a route form a loop, so the chain never terminates. |
| `dangling_alias` | An alias, `entry`, or fallback endpoint names something that does not exist. |
| `duplicate_priority` | Two or more routes declare the same `priority`. |
| `invalid_schema` | The document loaded, but does not match the schema (missing `nodes`, wrong types, duplicate ids). |
| `no_terminal_model` | A route has no node that can end the chain — mark one `terminal: true` or leave one without outgoing fallbacks. |
| `parse_error` | The file is not valid YAML. |
| `unreachable_node` | A node is neither the entry node nor reachable from it via fallbacks. |

`parse_error` and `invalid_schema` are fatal: no graph is built, so no structural
checks run and no other codes are reported for that file.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Clean — no diagnostics. |
| `1` | Diagnostics found. |
| `2` | Usage error, unreadable file, or a document that could not be parsed or did not match the schema (`parse_error`, `invalid_schema`). |

This makes the linter usable as a CI gate: `route-audit lint routes.yml` fails the
build on either a broken config (`1`) or a broken file (`2`).

## Development

```bash
make install   # pip install -e ".[dev]"
make test      # python3 -m pytest -q
```

`make test` runs the full suite: parser, checks, CLI, and the assertions this README
makes about itself.

## License

MIT. See [LICENSE](LICENSE).
