"""
LP relaxation + rounding baseline for ILP source placement.

This module keeps the same candidate-tree-based source placement model as the
full ILP, but relaxes x_{r,t} from binary to continuous variables:

    0 <= x_{r,t} <= 1

The fractional LP solution is rounded into selected trees, and the selected
trees are converted into a source placement. The output is still only a source
placement; entanglement routing, swapping, and fusion are performed later by
the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import gurobipy as gp
    from gurobipy import GRB
except ImportError as exc:
    raise ImportError(
        "gurobipy is required for the LP relaxation + rounding module."
    ) from exc

from ilp_multipartite_source_placement import (
    GUROBI_SEED_MAX,
    CandidateTree,
    MultipartiteRequest,
    allocate_redundant_source_placement,
    build_batch_user_path_edges,
    build_candidate_trees_for_requests,
    ilp_objective_mode,
    norm_edge,
    requests_from_user_sets,
    served_request_priority,
    status_to_string,
    tree_expected_throughput_value,
)
from network_topology import Topology
from seed_utils import derive_seed, set_global_seed


Edge = Tuple[Any, Any]


@dataclass
class LPRelaxationResult:
    status: int
    status_name: str
    objective: float
    expected_throughput_term: float
    objective_mode: str
    x_values: Dict[Tuple[int, int], float]
    z_values: Dict[Edge, float]
    model: Any


def solve_source_placement_lp_relaxation(
    graph,
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    max_trees_per_request: int = 1,
    solver_seed: Optional[int] = None,
    verbose: bool = False,
) -> LPRelaxationResult:
    edges = sorted(norm_edge(u, v) for u, v in graph.edges())
    nodes = sorted(graph.nodes())

    if node_memory is None:
        node_memory = {v: 10**6 for v in nodes}

    model = gp.Model("lp_relaxation_source_placement")
    if not verbose:
        model.Params.OutputFlag = 0
    if solver_seed is not None:
        model.Params.Seed = int(solver_seed) % GUROBI_SEED_MAX

    max_trees_per_request = max(1, int(max_trees_per_request))

    x = {}
    y = {}
    z = {
        edge: model.addVar(
            lb=0.0,
            ub=max_sources_per_edge,
            vtype=GRB.CONTINUOUS,
            name=f"z_{edge[0]}_{edge[1]}",
        )
        for edge in edges
    }
    for req in requests:
        y[req.request_id] = model.addVar(
            lb=0.0,
            ub=1.0,
            vtype=GRB.CONTINUOUS,
            name=f"y_r{req.request_id}",
        )
        for tree in candidate_trees.get(req.request_id, []):
            x[(req.request_id, tree.tree_id)] = model.addVar(
                lb=0.0,
                ub=1.0,
                vtype=GRB.CONTINUOUS,
                name=f"x_r{req.request_id}_t{tree.tree_id}",
            )

    model.update()
    objective_mode = ilp_objective_mode()
    if objective_mode not in {
        "expected_throughput",
        "coverage_expected_throughput",
        "coverage_expected_throughput_with_redundancy",
    }:
        raise ValueError(f"Unsupported ILP objective_mode={objective_mode!r}.")

    coverage_terms = []
    if objective_mode in {
        "coverage_expected_throughput",
        "coverage_expected_throughput_with_redundancy",
    }:
        request_priority = served_request_priority(candidate_trees)
        coverage_terms = [
            request_priority * y[req.request_id]
            for req in requests
        ]

    # LP-rounding uses the same expected-throughput surrogate as the full ILP,
    # with x and y relaxed to continuous variables. The ILP probabilities are
    # expected values; realized throughput is sampled later by the simulator.
    expected_terms = [
        tree_expected_throughput_value(req, tree) * x[(req.request_id, tree.tree_id)]
        for req in requests
        for tree in candidate_trees.get(req.request_id, [])
    ]
    model.setObjective(
        gp.quicksum(coverage_terms + expected_terms),
        GRB.MAXIMIZE,
    )

    for req in requests:
        selected_for_request = gp.quicksum(
            x[(req.request_id, tree.tree_id)]
            for tree in candidate_trees.get(req.request_id, [])
        )
        model.addConstr(
            selected_for_request >= y[req.request_id],
            name=f"request_coverage_lower_r{req.request_id}",
        )
        model.addConstr(
            selected_for_request <= max_trees_per_request * y[req.request_id],
            name=f"request_tree_limit_r{req.request_id}",
        )

    for edge in edges:
        selected_edge_load = gp.quicksum(
            x[(req.request_id, tree.tree_id)]
            for req in requests
            for tree in candidate_trees.get(req.request_id, [])
            if edge in tree.edges
        )
        model.addConstr(
            selected_edge_load <= z[edge],
            name=f"edge_{edge[0]}_{edge[1]}",
        )
        model.addConstr(
            z[edge] <= selected_edge_load,
            name=f"edge_source_exact_without_redundancy_{edge[0]}_{edge[1]}",
        )

    model.addConstr(
        gp.quicksum(
            source_cost * z[edge]
            for edge in edges
        )
        <= source_budget,
        name="source_budget",
    )

    for node in nodes:
        model.addConstr(
            gp.quicksum(
                tree.memory.get(node, 0) * x[(req.request_id, tree.tree_id)]
                for req in requests
                for tree in candidate_trees.get(req.request_id, [])
            )
            <= int(node_memory.get(node, 0)),
            name=f"memory_{node}",
        )
        model.addConstr(
            gp.quicksum(
                z[edge]
                for edge in edges
                if node in edge
            )
            <= int(node_memory.get(node, 0)),
            name=f"memory_source_placement_{node}",
        )

    model.optimize()
    status = model.Status
    if status not in [GRB.OPTIMAL, GRB.SUBOPTIMAL]:
        return LPRelaxationResult(
            status=status,
            status_name=status_to_string(status),
            objective=0.0,
            expected_throughput_term=0.0,
            objective_mode=objective_mode,
            x_values={},
            z_values={},
            model=model,
        )

    expected_throughput_term = sum(
        tree_expected_throughput_value(req, tree)
        * float(x[(req.request_id, tree.tree_id)].X)
        for req in requests
        for tree in candidate_trees.get(req.request_id, [])
    )
    return LPRelaxationResult(
        status=status,
        status_name=status_to_string(status),
        objective=float(model.ObjVal),
        expected_throughput_term=float(expected_throughput_term),
        objective_mode=objective_mode,
        x_values={key: float(var.X) for key, var in x.items()},
        z_values={edge: float(var.X) for edge, var in z.items()},
        model=model,
    )


def round_lp_solution_to_source_placement(
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    x_values: Dict[Tuple[int, int], float],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    max_trees_per_request: int = 1,
    fallback_edges: Optional[Iterable[Edge]] = None,
    epsilon: float = 1e-9,
) -> Dict[str, Any]:
    if node_memory is None:
        nodes = {
            node
            for trees in candidate_trees.values()
            for tree in trees
            for node in tree.memory
        }
        node_memory = {v: 10**6 for v in nodes}

    candidate_items = []
    for req in requests:
        for tree in candidate_trees.get(req.request_id, []):
            value = x_values.get((req.request_id, tree.tree_id), 0.0)
            if value <= epsilon:
                continue
            candidate_items.append((req, tree, value))

    candidate_items.sort(
        key=lambda item: (
            -(item[2] * tree_expected_throughput_value(item[0], item[1])),
            len(item[1].edges),
            -item[1].rho,
            item[0].request_id,
            item[1].tree_id,
        )
    )

    max_trees_per_request = max(1, int(max_trees_per_request))
    selected_tree_keys = set()
    selected_trees = []
    served_requests = set()
    selected_count_by_request: Dict[int, int] = {}
    edge_load: Dict[Edge, int] = {}
    memory_load: Dict[Any, int] = {}
    source_memory_load: Dict[Any, int] = {}
    used_budget = 0

    def try_select(req: MultipartiteRequest, tree: CandidateTree, value: float) -> bool:
        nonlocal used_budget
        tree_key = (req.request_id, tree.tree_id)
        if tree_key in selected_tree_keys:
            return False
        if selected_count_by_request.get(req.request_id, 0) >= max_trees_per_request:
            return False
        tree_budget = source_cost * len(tree.edges)
        if used_budget + tree_budget > source_budget:
            return False

        violates_edge = any(
            edge_load.get(edge, 0) + 1 > max_sources_per_edge
            for edge in tree.edges
        )
        if violates_edge:
            return False

        violates_memory = any(
            memory_load.get(node, 0) + amount > int(node_memory.get(node, 0))
            for node, amount in tree.memory.items()
        )
        if violates_memory:
            return False

        tree_source_memory: Dict[Any, int] = {}
        for edge in tree.edges:
            u, v = edge
            tree_source_memory[u] = tree_source_memory.get(u, 0) + 1
            tree_source_memory[v] = tree_source_memory.get(v, 0) + 1
        violates_source_memory = any(
            source_memory_load.get(node, 0) + amount > int(node_memory.get(node, 0))
            for node, amount in tree_source_memory.items()
        )
        if violates_source_memory:
            return False

        selected_tree_keys.add(tree_key)
        selected_count_by_request[req.request_id] = selected_count_by_request.get(req.request_id, 0) + 1
        served_requests.add(req.request_id)
        used_budget += tree_budget

        for edge in tree.edges:
            edge_load[edge] = edge_load.get(edge, 0) + 1
        for node, amount in tree_source_memory.items():
            source_memory_load[node] = source_memory_load.get(node, 0) + amount
        for node, amount in tree.memory.items():
            memory_load[node] = memory_load.get(node, 0) + amount

        selected_trees.append(
            {
                "request_id": req.request_id,
                "terminals": req.terminals,
                "tree_id": tree.tree_id,
                "edges": tree.edges,
                "swap_nodes": tree.swap_nodes,
                "fusion_nodes": tree.fusion_nodes,
                "memory": tree.memory,
                "rho": tree.rho,
                "lp_value": value,
            }
        )
        return True

    for req in requests:
        for item_req, tree, value in candidate_items:
            if item_req.request_id == req.request_id and try_select(item_req, tree, value):
                break

    for req, tree, value in candidate_items:
        try_select(req, tree, value)

    redundant = allocate_redundant_source_placement(
        selected_trees=selected_trees,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=source_cost,
        candidate_trees=candidate_trees,
        fallback_edges=fallback_edges,
        enforce_node_memory_for_redundancy=True,
    )

    return {
        "routing_source_placement": redundant["routing_source_placement"],
        "minimum_routing_source_placement": dict(sorted(edge_load.items())),
        "source_placement": redundant["routing_source_placement"],
        "selected_trees": selected_trees,
        "served_requests": sorted(served_requests),
        "minimum_used_budget": used_budget,
        "used_budget": redundant["used_budget"],
        "memory_load": redundant["memory_load"],
        "minimum_memory_load": memory_load,
        "minimum_source_memory_load": source_memory_load,
        "redundant_enforce_node_memory": True,
        "candidate_edge_count": redundant["candidate_edge_count"],
        "effective_capacity": redundant["effective_capacity"],
    }


def solve_single_slot_lp_rounding_request_batch(
    edge_list: List[tuple],
    request_batch: Iterable[Iterable[Any]],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    node_memory_capacity: Optional[int] = None,
    k_trees_per_request: int = 10,
    p_op: float = 0.8,
    q_swap: float = 1.0,
    q_fus: float = 1.0,
    q_rem: float = 1.0,
    master_seed: Optional[int] = None,
    solver_seed: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    set_global_seed(master_seed)

    topo = Topology(edge_list)
    graph = topo.graph
    requests = requests_from_user_sets(request_batch)

    if node_memory is None and node_memory_capacity is not None:
        node_memory = {v: int(node_memory_capacity) for v in topo.get_nodes()}

    # LP-R is the relaxation/rounding baseline for the same candidate pool used
    # by the full ILP.
    candidate_seed = derive_seed(master_seed, "ilp", "candidate-trees")
    lp_solver_seed = solver_seed
    if lp_solver_seed is None:
        lp_solver_seed = derive_seed(master_seed, "lp-round", "solver")

    candidate_trees = build_candidate_trees_for_requests(
        graph=graph,
        requests=requests,
        k_trees_per_request=k_trees_per_request,
        p_op=p_op,
        q_swap=q_swap,
        q_fus=q_fus,
        q_rem=q_rem,
        rho_min=0.0,
        weight_attr="length_km",
        seed=candidate_seed,
    )

    lp_result = solve_source_placement_lp_relaxation(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=1,
        max_trees_per_request=max(1, int(k_trees_per_request)),
        solver_seed=lp_solver_seed,
        verbose=verbose,
    )

    rounded = round_lp_solution_to_source_placement(
        requests=requests,
        candidate_trees=candidate_trees,
        x_values=lp_result.x_values,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=1,
        max_trees_per_request=max(1, int(k_trees_per_request)),
        fallback_edges=build_batch_user_path_edges(graph, requests),
    )

    result = {
        "status": lp_result.status,
        "status_name": lp_result.status_name,
        "objective": lp_result.objective,
        "lp_objective": lp_result.objective,
        "ilp_expected_objective": lp_result.objective,
        "ilp_expected_throughput_term": lp_result.expected_throughput_term,
        "ilp_covered_requests": len(rounded["served_requests"]),
        "ilp_selected_tree_count": len(rounded["selected_trees"]),
        "deployed_source_count": sum(rounded["source_placement"].values()),
        "objective_mode": lp_result.objective_mode,
        "x_values": lp_result.x_values,
        "source_placement": rounded["source_placement"],
        "routing_source_placement": rounded["routing_source_placement"],
        "selected_trees": rounded["selected_trees"],
        "served_requests": rounded["served_requests"],
        "used_budget": rounded["used_budget"],
        "memory_load": rounded["memory_load"],
        "candidate_edge_count": rounded["candidate_edge_count"],
        "effective_capacity": rounded["effective_capacity"],
        "throughput_qbps": len(rounded["served_requests"]),
        "throughput_selected_trees": len(rounded["selected_trees"]),
        "throughput_covered_requests": len(rounded["served_requests"]),
        "request_batch": [list(req) for req in request_batch],
        "master_seed": master_seed,
        "candidate_seed": candidate_seed,
        "solver_seed": None if lp_solver_seed is None else int(lp_solver_seed) % GUROBI_SEED_MAX,
        "candidate_tree_counts": {
            req_id: len(trees) for req_id, trees in candidate_trees.items()
        },
    }
    return result


def main() -> None:
    import single_slot_throughput_sweep_conditions as conditions

    edge_list = [
        (0, 1, 0),
        (1, 2, 0),
        (0, 3, 0),
        (3, 4, 0),
        (2, 4, 0),
        (1, 3, 0),
    ]
    request_batch = [[0, 2, 4], [0, 1, 3]]
    result = solve_single_slot_lp_rounding_request_batch(
        edge_list=edge_list,
        request_batch=request_batch,
        source_budget=20,
        max_sources_per_edge=4,
        k_trees_per_request=4,
        p_op=1.0,
        master_seed=conditions.RANDOM_SEED,
        verbose=False,
    )
    assert result["status_name"] in {"OPTIMAL", "SUBOPTIMAL"}
    assert "routing_source_placement" in result
    assert result["throughput_selected_trees"] >= 1
    print("LP relaxation + rounding test passed.")
    print(f"Status: {result['status_name']}")
    print(f"LP expected objective: {result['lp_objective']}")
    print(f"Selected trees: {result['throughput_selected_trees']}")
    print(f"Routing source placement: {result['routing_source_placement']}")


if __name__ == "__main__":
    main()
