"""
Betweenness-based quantum source placement baseline.

Edges with higher edge betweenness centrality are prioritized. This is a global
topology-based baseline and does not depend on individual requests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import networkx as nx


Edge = Tuple[Any, Any]
PAIR_COST = 1


def norm_edge(u: Any, v: Any) -> Edge:
    return (u, v) if u <= v else (v, u)


class BetweennessSourcePlacement:
    def __init__(self, topo):
        self.topo = topo
        self.sources: List[Edge] = []
        self.edge_scores: Dict[Edge, float] = {}

    def compute_edge_scores(self) -> Dict[Edge, float]:
        scores = nx.edge_betweenness_centrality(
            self.topo.graph,
            normalized=True,
            weight="length_km",
        )
        self.edge_scores = {
            norm_edge(u, v): float(score)
            for (u, v), score in scores.items()
        }
        return self.edge_scores

    def place_sources(
        self,
        cost_budget: int,
        max_per_edge: int = 1,
    ) -> List[Edge]:
        if cost_budget < 0:
            raise ValueError("cost_budget must be non-negative.")
        if max_per_edge < 1:
            raise ValueError("max_per_edge must be >= 1.")

        scores = self.compute_edge_scores()
        ranked_edges = sorted(
            scores,
            key=lambda edge: (-scores[edge], edge[0], edge[1]),
        )

        budget_pairs = cost_budget // PAIR_COST
        self.sources = []
        per_edge_count = {edge: 0 for edge in ranked_edges}

        while len(self.sources) < budget_pairs:
            placed_in_round = False
            for edge in ranked_edges:
                if len(self.sources) >= budget_pairs:
                    break
                if per_edge_count[edge] >= max_per_edge:
                    continue
                self.sources.append(edge)
                per_edge_count[edge] += 1
                placed_in_round = True

            if not placed_in_round:
                break

        return list(self.sources)

    def compute_cost(self) -> int:
        return len(self.sources) * PAIR_COST

    def allocation(self) -> Dict[Edge, int]:
        result: Dict[Edge, int] = {}
        for edge in self.sources:
            result[edge] = result.get(edge, 0) + 1
        return dict(sorted(result.items()))


def main() -> None:
    from network_topology import Topology

    edge_list = [
        (0, 1, 1),
        (1, 2, 1),
        (1, 3, 1),
        (3, 4, 1),
    ]
    topo = Topology(edge_list)
    placer = BetweennessSourcePlacement(topo)
    sources = placer.place_sources(cost_budget=4, max_per_edge=2)

    assert len(sources) == 4
    assert placer.compute_cost() == 4
    assert sources[0] == (1, 3) or sources[0] == (0, 1)
    assert all(count <= 2 for count in placer.allocation().values())

    print("BetweennessSourcePlacement test passed.")
    print("Sources:", sources)
    print("Allocation:", placer.allocation())
    print("Scores:", placer.edge_scores)


if __name__ == "__main__":
    main()
