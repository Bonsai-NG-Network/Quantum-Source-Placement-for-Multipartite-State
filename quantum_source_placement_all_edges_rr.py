PAIR_COST = 1


class AllEdgesRoundRobinSourcePlacement:
    """Baseline source placement: allocate sources across all physical edges."""

    def __init__(self, topo):
        self.topo = topo
        self.sources = []

    def place_sources_for_request(self, user_set=None, method="NOP", cost_budget=None, max_per_edge=1):
        """
        Return source placements as repeated edge tuples.

        The user_set argument is accepted for API compatibility with other source
        placement strategies, but this baseline ignores it and uses all edges.
        """
        if method not in {"NOP", "ALL_EDGES_RR", "all_edges_rr", "all_edges"}:
            raise ValueError(f"Unknown source placement method: {method}")

        base_keys = sorted({tuple(sorted(edge[:2])) for edge in self.topo.get_edges()})
        if not base_keys:
            self.sources = []
            print("[AllEdgesRoundRobinSourcePlacement] No candidate edges found.")
            return self.sources

        self.sources = []
        per_edge_count = {edge: 0 for edge in base_keys}

        if cost_budget is None:
            target_pairs = len(base_keys)
        else:
            if cost_budget < 0:
                raise ValueError("cost_budget must be non-negative")
            if cost_budget % PAIR_COST != 0:
                usable_budget = cost_budget - cost_budget % PAIR_COST
                print(
                    f"[AllEdgesRoundRobinSourcePlacement][WARN] cost_budget={cost_budget} is not divisible "
                    f"by pair cost {PAIR_COST}; using {usable_budget} instead."
                )
            target_pairs = cost_budget // PAIR_COST

        capacity_pairs = len(base_keys) * max_per_edge
        target_pairs = min(target_pairs, capacity_pairs)

        placed = 0
        idx = 0
        while placed < target_pairs:
            edge = base_keys[idx % len(base_keys)]
            if per_edge_count[edge] < max_per_edge:
                self.sources.append(edge)
                per_edge_count[edge] += 1
                placed += 1
            idx += 1
            if idx >= len(base_keys) and all(count >= max_per_edge for count in per_edge_count.values()):
                break

        print(f"[AllEdgesRoundRobinSourcePlacement] Method: {method}, Sources placed: {self.sources}")
        print(f"[AllEdgesRoundRobinSourcePlacement] Total cost: {self.compute_cost()} (target={target_pairs})")
        print(
            f"[AllEdgesRoundRobinSourcePlacement] Cost budget: {cost_budget}, max_per_edge={max_per_edge}, "
            f"capacity_pairs={capacity_pairs}, used_pairs={placed}"
        )
        return self.sources

    def compute_cost(self):
        return len(self.sources) * PAIR_COST


def main():
    from network_topology import Topology

    edge_list = [
        (0, 1, 10),
        (0, 3, 10),
        (1, 2, 10),
        (1, 4, 10),
        (2, 5, 10),
        (3, 4, 10),
        (3, 6, 10),
        (4, 7, 10),
        (5, 8, 10),
        (6, 7, 10),
        (7, 8, 10),
    ]
    topo = Topology(edge_list)
    placer = AllEdgesRoundRobinSourcePlacement(topo)
    sources = placer.place_sources_for_request(method="NOP", cost_budget=19, max_per_edge=2)
    assert len(sources) == 19
    assert placer.compute_cost() == 19
    print("AllEdgesRoundRobinSourcePlacement test passed.")


if __name__ == "__main__":
    main()
