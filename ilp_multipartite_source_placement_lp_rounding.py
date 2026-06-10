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
    build_candidate_trees_for_requests,
    norm_edge,
    requests_from_user_sets,
    status_to_string,
)
from network_topology import Topology
from seed_utils import derive_seed, set_global_seed


Edge = Tuple[Any, Any]


@dataclass
class LPRelaxationResult:
    status: int
    status_name: str
    objective: float
    x_values: Dict[Tuple[int, int], float]
    model: Any


def solve_source_placement_lp_relaxation(
    graph,
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
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

    x = {}
    for req in requests:
        for tree in candidate_trees.get(req.request_id, []):
            x[(req.request_id, tree.tree_id)] = model.addVar(
                lb=0.0,
                ub=1.0,
                vtype=GRB.CONTINUOUS,
                name=f"x_r{req.request_id}_t{tree.tree_id}",
            )

    model.update()
    model.setObjective(gp.quicksum(x.values()), GRB.MAXIMIZE)

    for req in requests:
        model.addConstr(
            gp.quicksum(
                x[(req.request_id, tree.tree_id)]
                for tree in candidate_trees.get(req.request_id, [])
            )
            <= 1,
            name=f"request_r{req.request_id}",
        )

    for edge in edges:
        model.addConstr(
            gp.quicksum(
                x[(req.request_id, tree.tree_id)]
                for req in requests
                for tree in candidate_trees.get(req.request_id, [])
                if edge in tree.edges
            )
            <= max_sources_per_edge,
            name=f"edge_{edge[0]}_{edge[1]}",
        )

    model.addConstr(
        gp.quicksum(
            source_cost * len(tree.edges) * x[(req.request_id, tree.tree_id)]
            for req in requests
            for tree in candidate_trees.get(req.request_id, [])
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

    model.optimize()
    status = model.Status
    if status not in [GRB.OPTIMAL, GRB.SUBOPTIMAL]:
        return LPRelaxationResult(
            status=status,
            status_name=status_to_string(status),
            objective=0.0,
            x_values={},
            model=model,
        )

    return LPRelaxationResult(
        status=status,
        status_name=status_to_string(status),
        objective=float(model.ObjVal),
        x_values={key: float(var.X) for key, var in x.items()},
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
            -item[2],
            len(item[1].edges),
            -item[1].rho,
            item[0].request_id,
            item[1].tree_id,
        )
    )

    selected_requests = set()
    selected_trees = []
    served_requests = set()
    edge_load: Dict[Edge, int] = {}
    memory_load: Dict[Any, int] = {}
    used_budget = 0

    for req, tree, value in candidate_items:
        if req.request_id in selected_requests:
            continue

        tree_budget = source_cost * len(tree.edges)
        if used_budget + tree_budget > source_budget:
            continue

        violates_edge = any(
            edge_load.get(edge, 0) + 1 > max_sources_per_edge
            for edge in tree.edges
        )
        if violates_edge:
            continue

        violates_memory = any(
            memory_load.get(node, 0) + amount > int(node_memory.get(node, 0))
            for node, amount in tree.memory.items()
        )
        if violates_memory:
            continue

        selected_requests.add(req.request_id)
        served_requests.add(req.request_id)
        used_budget += tree_budget

        for edge in tree.edges:
            edge_load[edge] = edge_load.get(edge, 0) + 1
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

    return {
        "routing_source_placement": dict(sorted(edge_load.items())),
        "source_placement": dict(sorted(edge_load.items())),
        "selected_trees": selected_trees,
        "served_requests": sorted(served_requests),
        "used_budget": used_budget,
        "memory_load": memory_load,
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

    candidate_seed = derive_seed(master_seed, "lp-round", "candidate-trees")
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
    )

    result = {
        "status": lp_result.status,
        "status_name": lp_result.status_name,
        "objective": lp_result.objective,
        "lp_objective": lp_result.objective,
        "x_values": lp_result.x_values,
        "source_placement": rounded["source_placement"],
        "routing_source_placement": rounded["routing_source_placement"],
        "selected_trees": rounded["selected_trees"],
        "served_requests": rounded["served_requests"],
        "used_budget": rounded["used_budget"],
        "memory_load": rounded["memory_load"],
        "throughput_qbps": len(rounded["selected_trees"]),
        "throughput_selected_trees": len(rounded["selected_trees"]),
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
        master_seed=1,
        verbose=False,
    )
    assert result["status_name"] in {"OPTIMAL", "SUBOPTIMAL"}
    assert "routing_source_placement" in result
    assert result["throughput_selected_trees"] >= 1
    print("LP relaxation + rounding test passed.")
    print(f"Status: {result['status_name']}")
    print(f"LP objective: {result['lp_objective']}")
    print(f"Selected trees: {result['throughput_selected_trees']}")
    print(f"Routing source placement: {result['routing_source_placement']}")


if __name__ == "__main__":
    main()
