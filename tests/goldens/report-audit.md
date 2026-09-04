# Coverage matrix

graph: examples/audit-routes.yml
policy: examples/policy.yml

| class | scenario | route | ok | selected | failure | hops | violations | attempted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eu-chat | healthy | chat | yes | primary | - | 0 | - | - |
| eu-chat | primary-down | chat | yes | backup | - | 1 | - | primary |
| eu-chat | two-down | chat | no | local | - | 2 | capability_downgrade, context_downgrade, max_hops_exceeded, provider_denied, region_denied | primary -> backup |
| hops | healthy | chat | yes | primary | - | 0 | - | - |
| hops | primary-down | chat | yes | backup | - | 1 | - | primary |
| hops | two-down | chat | no | local | - | 2 | capability_downgrade, context_downgrade, max_hops_exceeded, provider_denied, region_denied | primary -> backup |
| structured | healthy | chat | yes | primary | - | 0 | - | - |
| structured | primary-down | chat | yes | backup | - | 1 | - | primary |
| structured | two-down | chat | no | - | exhaustion | 3 | max_hops_exceeded | primary -> backup -> local |
| tools | healthy | chat | yes | primary | - | 0 | - | - |
| tools | primary-down | chat | yes | backup | - | 1 | - | primary |
| tools | two-down | chat | no | local | - | 2 | capability_downgrade, context_downgrade, max_hops_exceeded, provider_denied, region_denied | primary -> backup |

## Untested edges

none

## Untested nodes

none

## Routes without selection

none
