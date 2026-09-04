# Coverage matrix

graph: examples/routes.yml
policy: examples/policy-clean.yml

| class | scenario | route | ok | selected | failure | hops | violations | attempted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tools | healthy | chat | yes | primary | - | 0 | - | - |

## Untested edges

- chat: primary -> backup

## Untested nodes

- chat.backup
- embed.only

## Routes without selection

- embed
