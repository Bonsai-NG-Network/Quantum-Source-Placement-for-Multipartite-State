"""
Exact validation demo for ilp_multipartite_source_placement.py on a 3x3 grid.

This demo is intentionally deterministic:
    - 3x3 grid network, row-major node IDs 0..8
    - 2 multipartite requests
    - 3 user nodes per request
    - 2 manually specified candidate trees per request
    - p_e is fixed to 1 and is not included in rho
    - rho only uses q_swap=0.9 and q_fus=0.8
    - budget B=24
    - Maximum number of sources allowed on edge = 3
    - Node memory capacity = 4
"""

from itertools import combinations, product

import networkx as nx

from ilp_multipartite_source_placement import (
    CandidateTree,
    MultipartiteRequest,
    norm_edge,
    solve_joint_source_placement_ilp,
)


GRID_ROWS = 3
GRID_COLS = 3
EDGE_LENGTH_KM = 10

SOURCE_COST = 1
MAX_SOURCES_PER_EDGE = 3
NODE_MEMORY_CAPACITY = 4
REQUEST_WEIGHT = 1.0
Q_SWAP = 0.9
Q_FUS = 0.8
RHO_MIN = 0.0
SOURCE_BUDGETS = [24]


def build_3x3_grid():
    graph = nx.Graph()
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            node = row * GRID_COLS + col
            if col + 1 < GRID_COLS:
                graph.add_edge(node, node + 1, length_km=EDGE_LENGTH_KM)
            if row + 1 < GRID_ROWS:
                graph.add_edge(node, node + GRID_COLS, length_km=EDGE_LENGTH_KM)
    return graph


def tree_success_probability(swap_nodes, fusion_nodes):
    return (Q_SWAP ** len(swap_nodes)) * (Q_FUS ** len(fusion_nodes))


def make_tree(tree_id, request_id, terminals, edge_list, swap_nodes, fusion_nodes):
    graph = nx.Graph()
    graph.add_edges_from(edge_list)
    edges = sorted(norm_edge(u, v) for u, v in edge_list)
    memory = {v: graph.degree(v) for v in graph.nodes()}
    rho = tree_success_probability(swap_nodes, fusion_nodes)

    if rho < RHO_MIN:
        raise ValueError(f"Tree {tree_id} rho={rho} is below rho_min={RHO_MIN}.")

    return CandidateTree(
        tree_id=tree_id,
        request_id=request_id,
        terminals=list(terminals),
        graph=graph,
        edges=edges,
        swap_nodes=list(swap_nodes),
        fusion_nodes=list(fusion_nodes),
        memory=memory,
        rho=rho,
    )


def build_requests_and_candidate_trees():
    requests = [
        MultipartiteRequest(
            request_id=0,
            terminals=[0, 2, 6],
            weight=REQUEST_WEIGHT,
            demand=2,
        ),
        MultipartiteRequest(
            request_id=1,
            terminals=[2, 6, 8],
            weight=REQUEST_WEIGHT,
            demand=2,
        ),
    ]

    candidate_trees = {
        0: [
            make_tree(
                tree_id=0,
                request_id=0,
                terminals=[0, 2, 6],
                edge_list=[(0, 1), (1, 2), (0, 3), (3, 6)],
                swap_nodes=[1, 3],
                fusion_nodes=[0],
            ),
            make_tree(
                tree_id=1,
                request_id=0,
                terminals=[0, 2, 6],
                edge_list=[(0, 3), (3, 4), (4, 5), (2, 5), (3, 6)],
                swap_nodes=[4, 5],
                fusion_nodes=[3],
            ),
        ],
        1: [
            make_tree(
                tree_id=2,
                request_id=1,
                terminals=[2, 6, 8],
                edge_list=[(2, 5), (5, 8), (6, 7), (7, 8)],
                swap_nodes=[5, 7],
                fusion_nodes=[8],
            ),
            make_tree(
                tree_id=3,
                request_id=1,
                terminals=[2, 6, 8],
                edge_list=[(2, 5), (5, 4), (4, 3), (3, 6), (4, 7), (7, 8)],
                swap_nodes=[5, 3, 7],
                fusion_nodes=[4],
            ),
        ],
    }
    return requests, candidate_trees


def request_demands(requests):
    return {req.request_id: req.demand for req in requests}


def node_memory(graph):
    return {v: NODE_MEMORY_CAPACITY for v in graph.nodes()}


def all_trees(candidate_trees):
    return [tree for trees in candidate_trees.values() for tree in trees]


def required_source_placement(selected_trees):
    placement = {}
    for tree in selected_trees:
        for edge in tree.edges:
            placement[edge] = placement.get(edge, 0) + 1
    return dict(sorted(placement.items()))


def selected_memory_usage(selected_trees):
    usage = {}
    for tree in selected_trees:
        for node, amount in tree.memory.items():
            usage[node] = usage.get(node, 0) + amount
    return dict(sorted((node, value) for node, value in usage.items() if value > 0))


def brute_force_optimum(
    requests,
    candidate_trees,
    source_budget,
    node_memory_by_node,
    demand_by_request,
):
    choices = []
    for req in requests:
        trees = candidate_trees[req.request_id]
        demand = demand_by_request[req.request_id]
        req_choices = []
        for count in range(min(demand, len(trees)) + 1):
            req_choices.extend(combinations(trees, count))
        choices.append(req_choices)

    best = None
    for selected_by_request in product(*choices):
        selected_trees = [tree for group in selected_by_request for tree in group]
        placement = required_source_placement(selected_trees)
        memory_usage = selected_memory_usage(selected_trees)

        total_source_cost = SOURCE_COST * sum(placement.values())
        if total_source_cost > source_budget:
            continue

        if any(count > MAX_SOURCES_PER_EDGE for count in placement.values()):
            continue

        if any(
            used > node_memory_by_node.get(node, 0)
            for node, used in memory_usage.items()
        ):
            continue

        objective = sum(tree.rho for tree in selected_trees)
        selected_ids = sorted(tree.tree_id for tree in selected_trees)
        candidate = (objective, selected_ids, placement, memory_usage)
        if best is None or objective > best[0]:
            best = candidate

    return best


def format_edges(edges):
    return ", ".join(f"{edge}" for edge in edges)


def print_network_info(graph):
    print("\n[Physical Network G=(V,E)]")
    print(f"  grid: {GRID_ROWS}x{GRID_COLS}, row-major nodes 0..8")
    print(f"  |V|={graph.number_of_nodes()}, |E|={graph.number_of_edges()}")
    print(f"  edges: {format_edges(sorted(norm_edge(u, v) for u, v in graph.edges()))}")


def print_parameters(demand_by_request):
    print("\n[Input Parameters]")
    print(f"  source budgets B: {SOURCE_BUDGETS}")
    print(f"  C_s: {SOURCE_COST}")
    print(f"  Z_e^max: {MAX_SOURCES_PER_EDGE}")
    print(f"  M_v: {NODE_MEMORY_CAPACITY} for every node")
    print(f"  w_r: {REQUEST_WEIGHT}")
    print(f"  q_swap: {Q_SWAP}")
    print(f"  q_fus: {Q_FUS}")
    print("  p_e: 1.0 for every edge, not included in rho")
    print(f"  rho_min: {RHO_MIN}")
    print(f"  D_r: {demand_by_request}")


def print_request_info(requests):
    print("\n[Requests]")
    for req in requests:
        print(
            f"  R{req.request_id}: terminals={req.terminals}, "
            f"weight={req.weight}, D_r={req.demand}"
        )


def print_candidate_tree_info(candidate_trees):
    print("\n[Candidate Trees and Precomputed Values]")
    for tree in all_trees(candidate_trees):
        rho_formula = (
            f"{Q_SWAP}^{len(tree.swap_nodes)} * "
            f"{Q_FUS}^{len(tree.fusion_nodes)}"
        )
        print(
            f"  T{tree.tree_id} for R{tree.request_id}: "
            f"edges=[{format_edges(tree.edges)}], "
            f"swap={tree.swap_nodes}, fusion={tree.fusion_nodes}, "
            f"rho={tree.rho:.6f} ({rho_formula}), "
            f"memory={dict(sorted(tree.memory.items()))}"
        )


def print_edge_incidence(graph, candidate_trees):
    trees = all_trees(candidate_trees)
    used_edges = sorted(
        {
            edge
            for tree in trees
            for edge in tree.edges
        }
    )

    print("\n[Edge-Tree Incidence a_{e,t}]")
    header = "  edge       " + "  ".join(f"T{tree.tree_id}" for tree in trees)
    print(header)
    for edge in used_edges:
        values = [
            "1" if edge in tree.edges else "0"
            for tree in trees
        ]
        print(f"  {str(edge):10s} " + "   ".join(values))


def print_budget_result(budget, expected, result, candidate_trees):
    selected_ids = sorted(item["tree_id"] for item in result["selected_trees"])
    selected_tree_objects = [
        tree for tree in all_trees(candidate_trees) if tree.tree_id in selected_ids
    ]
    required_placement = required_source_placement(selected_tree_objects)
    memory_usage = selected_memory_usage(selected_tree_objects)

    print(f"\n[Budget B={budget}]")
    print(f"  brute force objective: {expected[0]:.6f}")
    print(f"  brute force trees:     {expected[1]}")
    print(f"  ILP objective:         {result['objective']:.6f}")
    print(f"  ILP selected trees:    {selected_ids}")
    print(f"  required z_e:          {required_placement}")
    print(f"  solver z_e:            {result['source_placement']}")
    print(f"  memory usage:          {memory_usage}")
    print(f"  total source cost:     {sum(required_placement.values())}")

    print("  checks:")
    print(f"    objective match:     {abs(result['objective'] - expected[0]) < 1e-9}")
    print(f"    tree set match:      {selected_ids == expected[1]}")
    print(f"    source budget:       {sum(required_placement.values()) <= budget}")
    print(
        "    per-edge cap:        "
        f"{all(count <= MAX_SOURCES_PER_EDGE for count in required_placement.values())}"
    )
    print(
        "    node memory:         "
        f"{all(value <= NODE_MEMORY_CAPACITY for value in memory_usage.values())}"
    )

    assert abs(result["objective"] - expected[0]) < 1e-9
    assert selected_ids == expected[1]


def main():
    graph = build_3x3_grid()
    requests, candidate_trees = build_requests_and_candidate_trees()
    demand_by_request = request_demands(requests)
    memory_by_node = node_memory(graph)

    print_network_info(graph)
    print_parameters(demand_by_request)
    print_request_info(requests)
    print_candidate_tree_info(candidate_trees)
    print_edge_incidence(graph, candidate_trees)

    for budget in SOURCE_BUDGETS:
        expected = brute_force_optimum(
            requests=requests,
            candidate_trees=candidate_trees,
            source_budget=budget,
            node_memory_by_node=memory_by_node,
            demand_by_request=demand_by_request,
        )

        result = solve_joint_source_placement_ilp(
            graph=graph,
            requests=requests,
            candidate_trees=candidate_trees,
            source_budget=budget,
            max_sources_per_edge=MAX_SOURCES_PER_EDGE,
            node_memory=memory_by_node,
            source_cost=SOURCE_COST,
            allow_multiple_trees_per_request=True,
            request_demands=demand_by_request,
            verbose=False,
        )

        print_budget_result(
            budget=budget,
            expected=expected,
            result=result,
            candidate_trees=candidate_trees,
        )

    print("\n3x3 ILP validation passed.")


if __name__ == "__main__":
    main()
