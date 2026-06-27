"""
LP relaxation + rounding baseline for ILP source placement.

This module keeps the same REPS-style source provisioning model as the full
ILP, but relaxes x_{r,t} and z_e to continuous variables:

    x_{r,t} >= 0
    0 <= z_e <= Z_e^max

LP-R keeps z_e in the LP relaxation, then rounds tree-service provisioning
variables first. The integer source placement is derived from rounded edge
demand by z_e = ceil(d_e / p_e). Entanglement routing, swapping, and fusion are
performed later by the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
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
    build_edge_generation_probabilities,
    build_candidate_trees_for_requests,
    norm_edge,
    requests_from_user_sets,
    status_to_string,
    tree_expected_throughput_value,
    validate_integer_provisioning_solution,
)
from network_topology import Topology
from seed_utils import derive_seed, set_global_seed


Edge = Tuple[Any, Any]


@dataclass
class LPRelaxationResult:
    status: int
    status_name: str
    objective: float
    operation_objective_term: float
    objective_mode: str
    x_values: Dict[Tuple[int, int], float]
    z_values: Dict[Edge, float]
    edge_generation_prob: Dict[Edge, float]
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
    p_op: float = 0.8,
    solver_seed: Optional[int] = None,
    verbose: bool = False,
) -> LPRelaxationResult:
    del max_trees_per_request
    edges = sorted(norm_edge(u, v) for u, v in graph.edges())
    nodes = sorted(graph.nodes())

    if node_memory is None:
        node_memory = {v: 10**6 for v in nodes}

    model = gp.Model("lp_relaxation_source_placement")
    if not verbose:
        model.Params.OutputFlag = 0
    if solver_seed is not None:
        model.Params.Seed = int(solver_seed) % GUROBI_SEED_MAX

    edge_generation_prob = build_edge_generation_probabilities(graph, edges, p_op=p_op)

    x = {}
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
        for tree in candidate_trees.get(req.request_id, []):
            x[(req.request_id, tree.tree_id)] = model.addVar(
                lb=0.0,
                vtype=GRB.CONTINUOUS,
                name=f"x_r{req.request_id}_t{tree.tree_id}",
            )

    model.update()
    objective_mode = "reps_source_provisioning_lp_relaxation"

    # LP-rounding relaxes the REPS source-provisioning model. Edge generation
    # probability appears only in the expected edge-capacity constraints.
    expected_terms = [
        tree_expected_throughput_value(req, tree) * x[(req.request_id, tree.tree_id)]
        for req in requests
        for tree in candidate_trees.get(req.request_id, [])
    ]
    model.setObjective(
        gp.quicksum(expected_terms),
        GRB.MAXIMIZE,
    )

    for edge in edges:
        selected_edge_load = gp.quicksum(
            x[(req.request_id, tree.tree_id)]
            for req in requests
            for tree in candidate_trees.get(req.request_id, [])
            if edge in tree.edges
        )
        model.addConstr(
            selected_edge_load <= edge_generation_prob[edge] * z[edge],
            name=f"expected_edge_capacity_{edge[0]}_{edge[1]}",
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
                z[edge]
                for edge in edges
                if node in edge
            )
            <= int(node_memory.get(node, 0)),
            name=f"node_memory_{node}",
        )

    model.optimize()
    status = model.Status
    if status not in [GRB.OPTIMAL, GRB.SUBOPTIMAL]:
        return LPRelaxationResult(
            status=status,
            status_name=status_to_string(status),
            objective=0.0,
            operation_objective_term=0.0,
            objective_mode=objective_mode,
            x_values={},
            z_values={},
            edge_generation_prob=edge_generation_prob,
            model=model,
        )

    operation_objective_term = sum(
        tree_expected_throughput_value(req, tree)
        * float(x[(req.request_id, tree.tree_id)].X)
        for req in requests
        for tree in candidate_trees.get(req.request_id, [])
    )
    return LPRelaxationResult(
        status=status,
        status_name=status_to_string(status),
        objective=float(model.ObjVal),
        operation_objective_term=float(operation_objective_term),
        objective_mode=objective_mode,
        x_values={key: float(var.X) for key, var in x.items()},
        z_values={edge: float(var.X) for edge, var in z.items()},
        edge_generation_prob=edge_generation_prob,
        model=model,
    )


def round_lp_solution_to_source_placement(
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    x_values: Dict[Tuple[int, int], float],
    z_values: Dict[Edge, float],
    edge_generation_prob: Dict[Edge, float],
    graph,
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    max_trees_per_request: int = 1,
    epsilon: float = 1e-9,
) -> Dict[str, Any]:
    del max_trees_per_request, z_values
    if node_memory is None:
        nodes = {
            endpoint
            for edge in edge_generation_prob
            for endpoint in edge
        }
        node_memory = {v: 10**6 for v in nodes}

    edges = sorted(edge_generation_prob)
    tree_lookup = {
        (req.request_id, tree.tree_id): (req, tree)
        for req in requests
        for tree in candidate_trees.get(req.request_id, [])
    }
    x_int: Dict[Tuple[int, int], int] = {key: 0 for key in tree_lookup}
    edge_demand: Dict[Edge, int] = {edge: 0 for edge in edges}

    def compute_source_placement(demand: Dict[Edge, int]) -> Optional[Dict[Edge, int]]:
        placement: Dict[Edge, int] = {}
        for edge in edges:
            d_e = int(demand.get(edge, 0))
            if d_e <= 0:
                continue
            p_e = float(edge_generation_prob.get(edge, 0.0))
            if p_e <= 0.0:
                return None
            placement[edge] = int(math.ceil((d_e - 1e-12) / p_e))
        return placement

    def feasibility_for(demand: Dict[Edge, int], x_candidate: Dict[Tuple[int, int], int]) -> Tuple[bool, Dict[str, Any]]:
        placement = compute_source_placement(demand)
        if placement is None:
            return False, {"feasible": False, "violations": ["positive demand on an edge with p_e=0"]}
        check = validate_integer_provisioning_solution(
            graph=graph,
            requests=requests,
            candidate_trees=candidate_trees,
            source_placement=placement,
            x_values={key: value for key, value in x_candidate.items() if value > 0},
            source_budget=source_budget,
            max_sources_per_edge=max_sources_per_edge,
            node_memory=node_memory,
            source_cost=source_cost,
            edge_generation_prob=edge_generation_prob,
        )
        return bool(check["feasible"]), check

    def additional_source_requirement(tree: CandidateTree, demand: Dict[Edge, int]) -> int:
        before = compute_source_placement(demand) or {}
        tentative = dict(demand)
        for edge in tree.edges:
            tentative[edge] = tentative.get(edge, 0) + 1
        after = compute_source_placement(tentative)
        if after is None:
            return 10**12
        return sum(after.values()) - sum(before.values())

    candidates = []
    for req in requests:
        for tree in candidate_trees.get(req.request_id, []):
            value = x_values.get((req.request_id, tree.tree_id), 0.0)
            if value <= epsilon:
                continue
            candidates.append(
                {
                    "key": (req.request_id, tree.tree_id),
                    "request": req,
                    "tree": tree,
                    "lp_value": float(value),
                    "floor_units": int(math.floor(float(value) + epsilon)),
                    "fractional": float(value) - math.floor(float(value) + epsilon),
                }
            )

    candidates.sort(
        key=lambda item: (
            -item["lp_value"] * tree_expected_throughput_value(item["request"], item["tree"]),
            -tree_expected_throughput_value(item["request"], item["tree"]),
            len(item["tree"].edges),
            additional_source_requirement(item["tree"], edge_demand),
            item["request"].request_id,
            item["tree"].tree_id,
        )
    )

    last_check: Dict[str, Any] = {
        "feasible": True,
        "violations": [],
        "used_budget": 0,
        "edge_demand": dict(edge_demand),
        "node_memory_load": {},
    }

    def try_add_service_unit(item: Dict[str, Any]) -> bool:
        nonlocal edge_demand, last_check
        key = item["key"]
        tree = item["tree"]
        tentative_x = dict(x_int)
        tentative_demand = dict(edge_demand)
        tentative_x[key] = tentative_x.get(key, 0) + 1
        for edge in tree.edges:
            tentative_demand[edge] = tentative_demand.get(edge, 0) + 1
        feasible, check = feasibility_for(tentative_demand, tentative_x)
        last_check = check
        if not feasible:
            return False
        x_int[key] = tentative_x[key]
        edge_demand = tentative_demand
        return True

    for item in candidates:
        for _ in range(max(0, int(item["floor_units"]))):
            try_add_service_unit(item)

    for item in candidates:
        if item["fractional"] > epsilon:
            try_add_service_unit(item)

    source_placement = compute_source_placement(edge_demand) or {}
    feasible, final_check = feasibility_for(edge_demand, x_int)
    last_check = final_check

    provisioned_units = []
    served_requests = set()
    rounded_objective = 0.0
    for key, units in sorted(x_int.items()):
        if units <= 0:
            continue
        req, tree = tree_lookup[key]
        served_requests.add(req.request_id)
        contribution = tree_expected_throughput_value(req, tree) * units
        rounded_objective += contribution
        provisioned_units.append(
            {
                "request_id": req.request_id,
                "terminals": req.terminals,
                "tree_id": tree.tree_id,
                "provisioned_units": units,
                "edges": tree.edges,
                "swap_nodes": tree.swap_nodes,
                "fusion_nodes": tree.fusion_nodes,
                "rho_op": tree.rho,
                "lp_value": x_values.get(key, 0.0),
                "objective_contribution": contribution,
            }
        )

    used_budget = source_cost * sum(source_placement.values())
    return {
        "routing_source_placement": dict(sorted(source_placement.items())),
        "minimum_routing_source_placement": dict(sorted(source_placement.items())),
        "source_placement": dict(sorted(source_placement.items())),
        "selected_trees": provisioned_units,
        "provisioned_tree_units": provisioned_units,
        "x_values": {key: value for key, value in x_int.items() if value > 0},
        "served_requests": sorted(served_requests),
        "minimum_used_budget": used_budget,
        "used_budget": used_budget,
        "memory_load": last_check.get("node_memory_load", {}),
        "candidate_edge_count": len(source_placement),
        "effective_capacity": sum(edge_generation_prob.get(edge, 0.0) * max_sources_per_edge for edge in source_placement),
        "edge_demand": dict(edge_demand),
        "rounded_integer_objective": rounded_objective,
        "rounding_feasible": bool(feasible),
        "feasibility_check": last_check,
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
        p_op=p_op,
        solver_seed=lp_solver_seed,
        verbose=verbose,
    )

    rounded = round_lp_solution_to_source_placement(
        requests=requests,
        candidate_trees=candidate_trees,
        x_values=lp_result.x_values,
        z_values=lp_result.z_values,
        edge_generation_prob=lp_result.edge_generation_prob,
        graph=graph,
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
        "rounded_integer_objective": rounded["rounded_integer_objective"],
        "ilp_expected_objective": rounded["rounded_integer_objective"],
        "ilp_operation_objective_term": lp_result.operation_objective_term,
        "ilp_provisioned_units": sum(item.get("provisioned_units", 1) for item in rounded["selected_trees"]),
        "deployed_source_count": sum(rounded["source_placement"].values()),
        "objective_mode": lp_result.objective_mode,
        "x_values": lp_result.x_values,
        "rounded_x_values": rounded["x_values"],
        "edge_generation_prob": lp_result.edge_generation_prob,
        "source_placement": rounded["source_placement"],
        "routing_source_placement": rounded["routing_source_placement"],
        "selected_trees": rounded["selected_trees"],
        "provisioned_tree_units": rounded["provisioned_tree_units"],
        "served_requests": rounded["served_requests"],
        "used_budget": rounded["used_budget"],
        "memory_load": rounded["memory_load"],
        "candidate_edge_count": rounded["candidate_edge_count"],
        "effective_capacity": rounded["effective_capacity"],
        "feasibility_check": rounded["feasibility_check"],
        "rounding_feasible": rounded["rounding_feasible"],
        "lp_rounding_note": (
            "LP-R solves the REPS LP relaxation and rounds service units first; "
            "the rounded integer solution is feasible when feasibility_check.feasible is true, "
            "but it is not guaranteed optimal."
        ),
        "throughput_qbps": None,
        "realized_throughput": None,
        "provisioning_surrogate_objective": rounded["rounded_integer_objective"],
        "throughput_selected_trees": sum(item.get("provisioned_units", 1) for item in rounded["selected_trees"]),
        "throughput_covered_requests": len(rounded["served_requests"]),
        "request_batch": [list(req) for req in request_batch],
        "master_seed": master_seed,
        "candidate_seed": candidate_seed,
        "solver_seed": None if lp_solver_seed is None else int(lp_solver_seed) % GUROBI_SEED_MAX,
        "request_demand_bound_enabled": False,
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
    print(f"Provisioned service units: {result['throughput_selected_trees']}")
    print(f"Routing source placement: {result['routing_source_placement']}")


if __name__ == "__main__":
    main()
