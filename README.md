# route-audit

Static routing-graph parser, linter, and offline route simulator for LLM route configs.

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

## Simulate

A graph can lint clean and still strand a request: the primary is down, the only node
with `tools` is rate limited, the request is larger than every context window left in
the chain. `simulate` walks one request through the fallback edges and reports which
nodes were attempted, why each was rejected, and which one was selected.

```bash
route-audit simulate routes.yml --request request.yml --scenario outage.yml
```

The walk is offline and deterministic — same graph, same request, same scenario, same
path, every time. Like `lint`, it **validates architecture and does not send or proxy
prompts**: it never contacts a provider and never learns whether a node is really up.

### The request fixture

A request declares the *shape* of a call, never its content. Keys that look like prompt
payload (`prompt`, `messages`, `system`, `input`, `text`, `content`, `query`, `user`,
`prompts`) are rejected as `invalid_schema` — there is nothing here for a prompt to
travel in.

```yaml
route: chat                          # required; a route id, node id, or alias
required_capabilities: [chat, tools] # optional; every one must be present on the node
context_tokens: 8000                 # optional non-negative integer
allowed_providers: [openai, anthropic]  # optional; empty means "any provider"
```

`required_capabilities` are matched against each node's declared `capabilities`,
`context_tokens` against its `context_limit`, and `allowed_providers` against its
`provider`. All three are compared as written; route-audit knows no provider catalogue
and estimates no token counts.

### The scenario

A scenario names nodes — by id or by alias — and marks what is wrong with each. Every
flag defaults to `false`, so an omitted node is healthy and an empty scenario is a
fully healthy run.

```yaml
route: chat                # optional; must resolve to the route the request takes
nodes:
  primary:
    unavailable: true      # the node is down
  chat-backup:             # a node alias, resolved inside the route
    rate_limited: true     # the node is up but refusing this request
  local:
    over_context: true     # the node cannot take this request's context
    missing_capabilities: [tools]  # capabilities the node has lost for this run
```

`unavailable`, `rate_limited`, and `over_context` are booleans; `missing_capabilities`
is a list subtracted from the node's declared `capabilities` for this run. A scenario
naming a node that is not in the route is an `unknown_scenario_node` failure rather
than a silently ignored line.

### The result

```bash
$ route-audit simulate examples/routes.yml --request examples/request.yml --scenario examples/outage.yml
selected chat.backup
  rejected chat.primary: unavailable, scenario marks the node unavailable
```

The first line is the outcome — `selected <route>.<node>`, or `failed <route>: <code>`
when the walk ran out of fallbacks. Each following line is one node on the attempted
path and the first reason it could not serve the request. `--json` emits the same walk
as `{"route", "ok", "selected", "failure", "attempted"}`, with `attempted` an ordered
list of `{"node_id", "reason", "detail"}`.

Reasons are checked in this order, first match wins:

| Reason | Meaning |
| --- | --- |
| `unavailable` | The scenario marks the node down. |
| `rate_limited` | The scenario marks the node rate limited — distinct from `unavailable`, so a report can tell a hard outage from backpressure. |
| `provider_not_allowed` | The node's `provider` is not in the request's `allowed_providers`. |
| `capability_mismatch` | The node lacks a `required_capability`, either by declaration or because the scenario took it away. |
| `context_overflow` | The request's `context_tokens` exceed the node's `context_limit`, or the scenario marks the node over context. |

A walk that selects nothing ends in one terminal failure:

| Failure | Meaning |
| --- | --- |
| `cycle` | The fallback edges loop back onto the current path — `lint` reports this first. |
| `empty_route` | The route named by the request has no nodes. |
| `exhaustion` | Every reachable node was attempted and rejected. |
| `route_mismatch` | The scenario declares a `route` other than the one the request takes. |
| `unknown_route` | The request's `route` is not a known route, node, or alias. |
| `unknown_scenario_node` | The scenario names a node that does not exist in this route. |

### Batch mode

`--batch` runs a matrix of requests against scenarios from one document and adds
coverage. The `seed` is required and recorded in the report: it fixes run order, so the
same batch against the same graph produces byte-identical output. It injects no
randomness — there is none to inject.

```yaml
seed: 20260904
requests:                  # named request fixtures, same schema as --request
  tools:
    route: chat
    required_capabilities: [chat, tools]
scenarios:                 # named scenarios, same schema as --scenario
  primary-down:
    route: chat
    nodes:
      primary:
        unavailable: true
matrix:                    # optional; omit it to run every request × every scenario
  - name: tools-healthy    # optional; defaults to "<request>+<scenario>"
    request: tools         # `scenario` omitted means the healthy scenario, "none"
  - name: tools-primary-down
    request: tools
    scenario: primary-down
```

```bash
$ route-audit simulate examples/routes.yml --batch examples/batch.yml
seed 20260904
case tools-healthy: selected chat.primary
case tools-primary-down: selected chat.backup
  rejected chat.primary: unavailable, scenario marks the node unavailable
coverage chat: cases=2 selections=2 attempted=[primary] selected=[backup, primary]
coverage embed: cases=0 selections=0 attempted=[] selected=[]
routes without selection: embed
```

Coverage answers the question a matrix is run to answer: which routes did these cases
actually exercise, which nodes did they attempt, and which ones were ever selected. A
route in `routes_without_selection` — `embed`, above — served nothing in the whole
batch, which usually means the matrix, not the graph, has a hole in it. Under `--json`
the same data lives at `coverage.routes`, one object per route, plus
`coverage.routes_without_selection`.

### Simulate exit codes

| Code | Meaning |
| --- | --- |
| `0` | A node was selected — every case, in batch mode. |
| `1` | The walk ended in terminal failure — any case, in batch mode. |
| `2` | Usage error, unreadable file, or a document that could not be parsed or did not match the schema. |

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

`make test` runs the full suite: parser, checks, simulation, CLI, and the assertions
this README makes about itself.

## License

MIT. See [LICENSE](LICENSE).
