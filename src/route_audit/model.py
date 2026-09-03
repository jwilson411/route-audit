"""The routing graph, after parsing and before linting.

Everything here is inert data. Nothing in this module talks to a
provider, reads credentials, or knows what any model actually costs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Node:
    """A provider/model pair inside a route."""

    id: str
    provider: str
    model: str
    terminal: bool | None = None
    capabilities: tuple[str, ...] = ()
    context_limit: int | None = None

    @property
    def qualified_model(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True, slots=True)
class FallbackEdge:
    """A directed `from -> to` fallback inside one route."""

    source: str
    target: str
    on: str | None = None
    index: int = 0


@dataclass(frozen=True, slots=True)
class Route:
    """A named entry point with its own node set and fallback edges."""

    id: str
    priority: int | None = None
    entry: str | None = None
    capabilities: tuple[str, ...] = ()
    context_limit: int | None = None
    nodes: tuple[Node, ...] = ()
    fallbacks: tuple[FallbackEdge, ...] = ()

    @property
    def path(self) -> str:
        return f"routes.{self.id}"

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.id for node in self.nodes)

    def node(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None


@dataclass(frozen=True, slots=True)
class Alias:
    """A name bound to a route or node.

    Aliases declared with `alias:`/`aliases:` on a route or node get a
    target that exists by construction. Aliases declared in the
    top-level `aliases:` block point at an arbitrary name, so they are
    the ones that can dangle.
    """

    name: str
    target: str
    path: str


@dataclass(frozen=True, slots=True)
class RouteGraph:
    routes: tuple[Route, ...] = ()
    aliases: tuple[Alias, ...] = ()

    def route(self, route_id: str) -> Route | None:
        for route in self.routes:
            if route.id == route_id:
                return route
        return None
