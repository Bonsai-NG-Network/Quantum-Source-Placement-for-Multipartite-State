"""
Small exact validation demo for ilp_multipartite_source_placement.py.

The instance is intentionally tiny and uses hand-built candidate trees, so the
ILP optimum can be checked by exhaustive enumeration.
"""

from itertools import product

import networkx as nx

from ilp_multipartite_source_placement import (
    CandidateTree,
    MultipartiteRequest,
    norm_edge,
    solve_joint_source_placement_ilp,
)


def make_tree(tree_id, request_id, terminals, edge_list, rho):
    graph = nx.Graph()
    graph.add_edges_from(edge_list)
    edges = sorted(norm_edge(u, v) for u, v in edge_list)
    memory = {v: graph.degree(v) for v in graph.nodes()}
    return CandidateTree(
        tree_id=tree_id,
        request_id=request_id,
        terminals=list(terminals),
        graph=graph,
        edges=edges,
        swap_nodes=[],
        fusion_nodes=[terminals[1]],
        memory=memory,
        rho=rho,
    )


def brute_force_optimum(requests, candidate_trees, source_budget, node_memory):
    choices = []
    for req in requests:
        trees = candidate_trees[req.request_id]
        choices.append([None] + trees)

    best = None
    for selected in product(*choices):
        used_edges = {}
        used_memory = {}
        objective = 0.0
        selected_ids = []

        feasible = True
        for req, tree in zip(requests, selected):
            if tree is None:
                continue

            objective += req.weight * tree.rho
            selected_ids.append(tree.tree_id)

            for edge in tree.edges:
                used_edges[edge] = used_edges.get(edge, 0) + 1

            for node, amount in tree.memory.items():
                used_memory[node] = used_memory.get(node, 0) + amount
                if used_memory[node] > node_memory.get(node, 0):
                    feasible = False

        if sum(used_edges.values()) > source_budget:
            feasible = False

        if not feasible:
            continue

        candidate = (objective, selected_ids, used_edges)
        if best is None or objective > best[0]:
            best = candidate

    return best


def main():
    graph = nx.Graph()
    graph.add_edges_from([(0, 1), (1, 2), (2, 3)])

    requests = [
        MultipartiteRequest(request_id=0, terminals=[0, 1, 2], weight=1.0),
        MultipartiteRequest(request_id=1, terminals=[1, 2, 3], weight=1.0),
    ]

    candidate_trees = {
        0: [
            make_tree(
                tree_id=0,
                request_id=0,
                terminals=[0, 1, 2],
                edge_list=[(0, 1), (1, 2)],
                rho=0.9,
            )
        ],
        1: [
            make_tree(
                tree_id=1,
                request_id=1,
                terminals=[1, 2, 3],
                edge_list=[(1, 2), (2, 3)],
                rho=0.8,
            )
        ],
    }

    node_memory = {0: 4, 1: 4, 2: 4, 3: 4}

    for budget in [3, 4]:
        expected = brute_force_optimum(
            requests=requests,
            candidate_trees=candidate_trees,
            source_budget=budget,
            node_memory=node_memory,
        )

        result = solve_joint_source_placement_ilp(
            graph=graph,
            requests=requests,
            candidate_trees=candidate_trees,
            source_budget=budget,
            max_sources_per_edge=2,
            node_memory=node_memory,
            verbose=False,
        )

        selected_ids = sorted(item["tree_id"] for item in result["selected_trees"])
        print(f"\nBudget B={budget}")
        print(f"  brute force objective: {expected[0]:.6f}")
        print(f"  brute force trees:     {expected[1]}")
        print(f"  ILP objective:         {result['objective']:.6f}")
        print(f"  ILP trees:             {selected_ids}")
        print(f"  ILP source placement:  {result['source_placement']}")

        assert abs(result["objective"] - expected[0]) < 1e-9
        assert selected_ids == expected[1]

    print("\nSmall validation passed.")


if __name__ == "__main__":
    main()
