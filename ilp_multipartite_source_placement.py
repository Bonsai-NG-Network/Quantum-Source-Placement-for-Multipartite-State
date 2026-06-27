"""
REPS-style multipartite source/link provisioning ILP.

The ILP decides global edge-level source placement z_e. Integer x_{r,t}
variables represent provisioning-stage multipartite service units over
candidate tree patterns, not final realized GHZ routing trees.

Realized throughput is measured later by the simulator after stochastic Bell
link generation and online multipartite routing over the successful-link graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Iterable, Optional, Any

import random
import networkx as nx

try:
    import gurobipy as gp
    from gurobipy import GRB
except ImportError as exc:
    raise ImportError(
        "gurobipy is required for this ILP module. "
        "Please install Gurobi and gurobipy first."
    ) from exc

from network_topology import Topology
from network_request import RequestGenerator
from steiner_tree_algorithms import approximate_steiner_tree
from tree_operation_planner import build_tree_operation_plan, probability_for
from seed_utils import derive_seed, set_global_seed


Edge = Tuple[Any, Any]
GUROBI_SEED_MAX = 2_000_000_000


def norm_edge(u: Any, v: Any) -> Edge:
    """Normalize an undirected edge as a sorted tuple."""
    return (u, v) if u <= v else (v, u)


def edge_success_prob(
    length_km: float,
    p_op: float = 0.8,
    loss_coef_db_per_km: float = 0.2,
) -> float:
    """
    Compute entanglement generation probability on one edge.

    Same physical-layer idea as the conference paper:
        p_e = p_op * 10^(-alpha * L / 10)

    where alpha is the fiber attenuation coefficient in dB/km.
    """
    transmittance = 10.0 ** (-(loss_coef_db_per_km * length_km) / 10.0)
    return float(p_op * transmittance)


def build_edge_generation_probabilities(
    graph: nx.Graph,
    edges: Iterable[Edge],
    p_op: float = 0.8,
) -> Dict[Edge, float]:
    """Return p_e for every physical edge used by the provisioning model."""
    edge_generation_prob: Dict[Edge, float] = {}
    for edge in edges:
        u, v = edge
        data = graph.get_edge_data(u, v, default={}) or {}
        length_km = float(data.get("length_km", data.get("weight", 1.0)))
        edge_generation_prob[edge] = edge_success_prob(length_km=length_km, p_op=p_op)
    return edge_generation_prob


def validate_integer_provisioning_solution(
    graph: nx.Graph,
    requests: List["MultipartiteRequest"],
    candidate_trees: Dict[int, List["CandidateTree"]],
    source_placement: Dict[Edge, int],
    x_values: Dict[Tuple[int, int], int],
    source_budget: int,
    max_sources_per_edge: int,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    edge_generation_prob: Optional[Dict[Edge, float]] = None,
    p_op: float = 0.8,
    tol: float = 1e-6,
) -> Dict[str, Any]:
    """
    Validate an integer REPS provisioning solution.

    Checks the source/link-generation attempt placement z_e and tree-service
    provisioning variables x_{r,t} against the source budget, per-edge source
    limit, expected edge-capacity constraint, node memory for incident
    source/link-generation attempts, and integer nonnegative domains.
    """
    edges = sorted(norm_edge(u, v) for u, v in graph.edges())
    nodes = sorted(graph.nodes())
    if node_memory is None:
        node_memory = {v: 10**6 for v in nodes}
    if edge_generation_prob is None:
        edge_generation_prob = build_edge_generation_probabilities(graph, edges, p_op=p_op)

    tree_lookup = {
        (req_id, tree.tree_id): tree
        for req_id, trees in candidate_trees.items()
        for tree in trees
    }
    z_all = {edge: int(source_placement.get(edge, 0)) for edge in edges}
    x_all = {key: int(value) for key, value in x_values.items() if int(value) != 0}
    violations: List[str] = []

    for edge, value in source_placement.items():
        if edge not in z_all:
            violations.append(f"source placement uses non-physical edge {edge}")
        if abs(float(value) - round(float(value))) > tol or int(round(float(value))) < 0:
            violations.append(f"z[{edge}] is not a nonnegative integer: {value}")

    for key, value in x_values.items():
        if key not in tree_lookup:
            violations.append(f"x{key} does not correspond to a candidate tree")
        if abs(float(value) - round(float(value))) > tol or int(round(float(value))) < 0:
            violations.append(f"x{key} is not a nonnegative integer: {value}")

    used_budget = source_cost * sum(z_all.values())
    if used_budget > source_budget + tol:
        violations.append(f"source budget violated: {used_budget} > {source_budget}")

    for edge, value in z_all.items():
        if value < -tol:
            violations.append(f"negative z[{edge}]={value}")
        if value > max_sources_per_edge + tol:
            violations.append(f"edge source limit violated on {edge}: {value} > {max_sources_per_edge}")

    node_load = {node: 0 for node in nodes}
    for edge, value in z_all.items():
        u, v = edge
        node_load[u] = node_load.get(u, 0) + value
        node_load[v] = node_load.get(v, 0) + value
    for node, load in node_load.items():
        cap = int(node_memory.get(node, 0))
        if load > cap + tol:
            violations.append(f"node memory violated at {node}: {load} > {cap}")

    edge_demand = {edge: 0 for edge in edges}
    for key, units in x_all.items():
        tree = tree_lookup.get(key)
        if tree is None:
            continue
        for edge in tree.edges:
            edge_demand[edge] = edge_demand.get(edge, 0) + units

    for edge in edges:
        demand = edge_demand.get(edge, 0)
        capacity = float(edge_generation_prob.get(edge, 0.0)) * z_all.get(edge, 0)
        if demand > capacity + tol:
            violations.append(
                f"expected edge-capacity violated on {edge}: demand {demand} > p_e z_e {capacity:.9g}"
            )

    return {
        "feasible": not violations,
        "violations": violations,
        "used_budget": used_budget,
        "source_placement": {edge: value for edge, value in z_all.items() if value > 0},
        "x_values": {key: value for key, value in x_all.items() if value > 0},
        "edge_demand": edge_demand,
        "edge_capacity": {
            edge: float(edge_generation_prob.get(edge, 0.0)) * z_all.get(edge, 0)
            for edge in edges
        },
        "node_memory_load": node_load,
    }


@dataclass
class MultipartiteRequest:
    """A multipartite request represented by a terminal/user set."""
    request_id: int
    terminals: List[Any]
    weight: float = 1.0
    demand: int = 1


@dataclass
class CandidateTree:
    """
    Candidate Steiner-like tree for one multipartite request.

    The `memory` field is retained only for backward-compatible object shape
    and is not used by the current REPS ILP, ILP-CG, or LP-R formulations.
    """
    tree_id: int
    request_id: int
    terminals: List[Any]
    graph: nx.Graph
    edges: List[Edge]
    swap_nodes: List[Any]
    fusion_nodes: List[Any]
    memory: Dict[Any, int]
    rho: float
    reduced_edges: Optional[List[Edge]] = None
    removal_nodes: Optional[List[Any]] = None


def tree_objective_value(tree: CandidateTree) -> float:
    """
    Operation-level utility for provisioning one candidate tree service unit.

    In the REPS-style source provisioning ILP, edge generation probability is
    handled by the expected edge-capacity constraint p_e z_e. The tree rho used
    in the objective includes only non-link operations such as swapping and
    fusion.
    """
    return float(tree.rho)


def tree_expected_throughput_value(
    request: MultipartiteRequest,
    tree: CandidateTree,
) -> float:
    """Return rho^{op}_{r,t} for the REPS provisioning objective."""
    del request
    return tree_objective_value(tree)


def ilp_objective_mode() -> str:
    """Return the active source-provisioning objective mode."""
    return "reps_source_provisioning"


def generate_diverse_steiner_trees(
    graph: nx.Graph,
    terminals: Iterable[Any],
    k_trees: int = 10,
    weight_attr: str = "length_km",
    jitter_ratio: float = 0.05,
    overlap_penalty: float = 0.5,
    seed: Optional[int] = None,
) -> List[nx.Graph]:
    """
    Generate multiple diverse Steiner-like trees.

    Args:
        graph: physical quantum network.
        terminals: user nodes of one request.
        k_trees: number of candidate trees to attempt.
        weight_attr: edge length attribute.
        jitter_ratio: random perturbation applied to edge weights.
        overlap_penalty: multiplicative penalty for edges already used by
            previously accepted trees.
        seed: random seed used for deterministic candidate generation.

    Returns:
        List of unique candidate Steiner-like trees.
    """
    terminals = list(terminals)

    if len(terminals) <= 1:
        return []

    rng = random.Random(seed)
    rebuilt_trees = []
    signatures = set()
    edge_usage: Dict[Edge, int] = {}
    max_attempts = max(k_trees * 8, k_trees)

    for attempt in range(max_attempts):
        weighted = nx.Graph()
        weighted.add_nodes_from(graph.nodes(data=True))
        for u, v, data in graph.edges(data=True):
            edge = norm_edge(u, v)
            base = float(data.get(weight_attr, data.get("weight", 1.0)))
            jitter = 1.0 + jitter_ratio * rng.random()
            penalty = 1.0 + overlap_penalty * edge_usage.get(edge, 0)
            attrs = dict(data)
            attrs[weight_attr] = base * jitter * penalty
            weighted.add_edge(u, v, **attrs)

        try:
            T = approximate_steiner_tree(weighted, terminals, weight_key=weight_attr)
        except nx.NetworkXException:
            continue

        signature = frozenset(norm_edge(u, v) for u, v in T.edges())
        if not signature or signature in signatures:
            continue

        H = nx.Graph()
        H.add_nodes_from(T.nodes(data=True))
        for u, v in T.edges():
            if graph.has_edge(u, v):
                H.add_edge(u, v, **graph[u][v])
            else:
                H.add_edge(u, v, **T[u][v])
        rebuilt_trees.append(H)
        signatures.add(signature)
        for edge in signature:
            edge_usage[edge] = edge_usage.get(edge, 0) + 1

        if len(rebuilt_trees) >= k_trees:
            break

    return rebuilt_trees


def identify_operation_nodes(
    tree: nx.Graph,
    terminals: Iterable[Any],
    fusion_policy: str = "branching",
) -> Tuple[List[Any], List[Any]]:
    """
    Identify swapping and fusion nodes for a candidate tree.

    Simple first model:
    - Non-terminal degree-2 nodes are swapping nodes.
    - Branching nodes with degree >= 3 are fusion nodes.
    - If no branching node exists, choose the highest-degree non-terminal node
      or a terminal as the fusion node.

    This can later be replaced by a protocol-specific GHZ construction rule.
    """
    if fusion_policy not in {"single_center", "branching"}:
        raise ValueError(f"Unknown fusion_policy: {fusion_policy}")

    plan = build_tree_operation_plan(tree, terminals)
    fusion_nodes = plan.fusion_nodes
    if fusion_policy == "single_center" and fusion_nodes:
        fusion_nodes = [max(fusion_nodes, key=lambda n: plan.reduced_tree.degree(n))]

    return plan.swap_nodes, fusion_nodes


def compute_tree_success_probability(
    tree: nx.Graph,
    swap_nodes: Iterable[Any],
    fusion_nodes: Iterable[Any],
    p_op: float = 0.8,
    q_swap: float = 1.0,
    q_fus: float = 1.0,
    q_rem: float = 1.0,
    removal_nodes: Optional[Iterable[Any]] = None,
    loss_coef_db_per_km: float = 0.2,
    weight_attr: str = "length_km",
) -> float:
    """
    Compute operation-level tree success probability:

        rho_t =
            prod_{v in V_swap} q_v^swap
            * prod_{v in V_fus} q_v^fus

    Edge generation probabilities are intentionally excluded here. They are
    represented in the REPS expected edge-capacity constraint p_e z_e.
    """
    del p_op, q_rem, loss_coef_db_per_km, weight_attr
    rho = 1.0

    for node in swap_nodes:
        rho *= probability_for(node, q_swap)

    for node in fusion_nodes:
        rho *= probability_for(node, q_fus)

    return float(rho)


def build_candidate_trees_for_requests(
    graph: nx.Graph,
    requests: List[MultipartiteRequest],
    k_trees_per_request: int = 10,
    p_op: float = 0.8,
    q_swap: float = 1.0,
    q_fus: float = 1.0,
    q_rem: float = 1.0,
    rho_min: float = 0.0,
    weight_attr: str = "length_km",
    seed: Optional[int] = None,
) -> Dict[int, List[CandidateTree]]:
    """
    Generate candidate trees for all requests.

    Important:
    - Candidate trees are generated per request.
    - Source placement is NOT generated per request.
    - Source placement is decided globally by the ILP through z_e.
    """
    all_candidate_trees: Dict[int, List[CandidateTree]] = {}
    global_tree_id = 0

    for req in requests:
        raw_trees = generate_diverse_steiner_trees(
            graph=graph,
            terminals=req.terminals,
            k_trees=k_trees_per_request,
            weight_attr=weight_attr,
            seed=derive_seed(seed, "request", req.request_id),
        )

        candidate_list: List[CandidateTree] = []

        for T in raw_trees:
            plan = build_tree_operation_plan(
                T,
                users=req.terminals,
                p_op=p_op,
                q_swap=q_swap,
                q_fus=q_fus,
                q_rem=q_rem,
                weight_attr=weight_attr,
            )
            swap_nodes = plan.swap_nodes
            fusion_nodes = plan.fusion_nodes
            removal_nodes = plan.candidate_removal_nodes

            rho = plan.rho

            if rho < rho_min:
                continue

            edges = sorted(norm_edge(u, v) for u, v in T.edges())
            reduced_edges = sorted(norm_edge(u, v) for u, v in plan.reduced_tree.edges())

            candidate_list.append(
                CandidateTree(
                    tree_id=global_tree_id,
                    request_id=req.request_id,
                    terminals=list(req.terminals),
                    graph=T,
                    edges=edges,
                    swap_nodes=list(swap_nodes),
                    fusion_nodes=list(fusion_nodes),
                    memory={},
                    rho=rho,
                    reduced_edges=reduced_edges,
                    removal_nodes=list(removal_nodes),
                )
            )
            global_tree_id += 1

        all_candidate_trees[req.request_id] = candidate_list

    return all_candidate_trees


def build_batch_user_path_edges(
    graph: nx.Graph,
    requests: List[MultipartiteRequest],
    k_paths: int = 2,
    weight_attr: str = "length_km",
) -> List[Edge]:
    """
    Build request-aware shortest-path edges for non-ILP heuristics.

    The current Full ILP, ILP-CG, and LP-R default paths do not use this for
    source placement post-processing.
    """
    users = []
    seen = set()
    for req in requests:
        for user in req.terminals:
            if user not in seen:
                seen.add(user)
                users.append(user)

    edges = set()
    for i, source in enumerate(users):
        for target in users[i + 1:]:
            try:
                paths = nx.shortest_simple_paths(
                    graph,
                    source=source,
                    target=target,
                    weight=weight_attr,
                )
                for path_idx, path in enumerate(paths):
                    if path_idx >= k_paths:
                        break
                    for u, v in zip(path, path[1:]):
                        edges.add(norm_edge(u, v))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

    return sorted(edges)


def solve_joint_source_placement_ilp(
    graph: nx.Graph,
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    p_op: float = 0.8,
    allow_multiple_trees_per_request: bool = False,
    demand_per_request: int = 1,
    request_demands: Optional[Dict[int, int]] = None,
    time_limit: Optional[float] = None,
    mip_gap: Optional[float] = None,
    solver_seed: Optional[int] = None,
    objective_mode: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Solve the REPS-style multipartite source provisioning ILP.

    This model decides edge-level source/link-generation attempts z_e. The
    x_{r,t} variables are integer provisioning-stage service units for
    candidate tree patterns, not final realized routing trees. Link generation
    realization and online GHZ routing are evaluated later by the simulator.

    Active model:

        max sum_{r,t} rho_op[r,t] x_{r,t}

        sum_e C_s z_e <= B
        0 <= z_e <= Z_e^max
        sum_{r,t:e in E_t} x_{r,t} <= p_e z_e
        sum_{e incident to v} z_e <= M_v

    Caller compatibility parameters unrelated to REPS provisioning are ignored.

    Returns:
        dict containing source placement z_e, provisioned tree service units,
        objective value, and Gurobi status.
    """
    del allow_multiple_trees_per_request, demand_per_request, request_demands
    objective_mode = "reps_source_provisioning"

    edges = sorted(norm_edge(u, v) for u, v in graph.edges())
    nodes = sorted(graph.nodes())

    if node_memory is None:
        # Default memory capacity: large enough not to bind.
        node_memory = {v: 10**6 for v in nodes}

    edge_generation_prob = build_edge_generation_probabilities(graph, edges, p_op=p_op)

    model = gp.Model("reps_multipartite_source_provisioning")

    if not verbose:
        model.Params.OutputFlag = 0

    if time_limit is not None:
        model.Params.TimeLimit = float(time_limit)

    if mip_gap is not None:
        model.Params.MIPGap = float(mip_gap)

    if solver_seed is not None:
        model.Params.Seed = int(solver_seed) % GUROBI_SEED_MAX

    # -----------------------------
    # Variables
    # -----------------------------
    z = {
        e: model.addVar(
            vtype=GRB.INTEGER,
            lb=0,
            ub=max_sources_per_edge,
            name=f"z_{e[0]}_{e[1]}",
        )
        for e in edges
    }

    x = {}
    for req in requests:
        for t in candidate_trees.get(req.request_id, []):
            x[(req.request_id, t.tree_id)] = model.addVar(
                vtype=GRB.INTEGER,
                lb=0,
                name=f"x_r{req.request_id}_t{t.tree_id}",
            )

    model.update()

    # -----------------------------
    # Objective
    # -----------------------------
    model.setObjective(
        gp.quicksum(
            tree_expected_throughput_value(req, t) * x[(req.request_id, t.tree_id)]
            for req in requests
            for t in candidate_trees.get(req.request_id, [])
        ),
        GRB.MAXIMIZE,
    )

    # -----------------------------
    # Constraint 1: source budget
    # -----------------------------
    model.addConstr(
        gp.quicksum(source_cost * z[e] for e in edges) <= source_budget,
        name="source_budget",
    )

    # -----------------------------
    # Constraint 2: REPS expected edge-capacity
    #
    # sum_{r,t} a_{e,t} x_{r,t} <= p_e z_e
    # -----------------------------
    for e in edges:
        selected_edge_load = gp.quicksum(
            x[(req.request_id, t.tree_id)]
            for req in requests
            for t in candidate_trees.get(req.request_id, [])
            if e in t.edges
        )
        model.addConstr(
            selected_edge_load <= edge_generation_prob[e] * z[e],
            name=f"expected_edge_capacity_{e[0]}_{e[1]}",
        )

    # -----------------------------
    # Constraint 3: Bell-link generation node-memory capacity
    #
    # sum_{e incident to v} z_e <= M_v
    # -----------------------------
    for v in nodes:
        model.addConstr(
            gp.quicksum(
                z[e]
                for e in edges
                if v in e
            ) <= int(node_memory.get(v, 0)),
            name=f"node_memory_{v}",
        )

    # -----------------------------
    # Solve
    # -----------------------------
    model.optimize()

    # -----------------------------
    # Extract solution
    # -----------------------------
    status = model.Status

    result = {
        "status": status,
        "status_name": status_to_string(status),
        "solution_status": "not_solved",
        "objective": None,
        "objective_bound": None,
        "mip_gap": None,
        "source_placement": {},
        "routing_source_placement": {},
        "selected_trees": [],
        "provisioned_tree_units": [],
        "x_values": {},
        "edge_generation_prob": edge_generation_prob,
        "served_requests": [],
        "request_demands": None,
        "request_demand_bound_enabled": False,
        "objective_mode": objective_mode,
        "ilp_expected_objective": None,
        "ilp_operation_objective_term": 0.0,
        "ilp_provisioned_units": 0,
        "deployed_source_count": 0,
        "solver_seed": solver_seed,
        "is_exact_over_candidate_set": False,
        "optimality_note": (
            "Full ILP is exact only over the pre-generated candidate tree set; "
            "it is not globally optimal over all multipartite trees unless that set is exhaustive."
        ),
        "feasibility_check": {"feasible": False, "violations": ["no solution extracted"]},
        "model": model,
    }

    if status == GRB.OPTIMAL:
        result["solution_status"] = "optimal"
        result["is_exact_over_candidate_set"] = True
    elif status in [GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
        result["solution_status"] = "best_incumbent"
    else:
        raise RuntimeError(f"ILP failed with status {status_to_string(status)} ({status})")

    if model.SolCount == 0:
        raise RuntimeError(f"ILP status {status_to_string(status)} but no incumbent solution is available")

    result["objective"] = float(model.ObjVal)
    result["ilp_expected_objective"] = float(model.ObjVal)
    try:
        result["objective_bound"] = float(model.ObjBound)
    except (gp.GurobiError, AttributeError):
        result["objective_bound"] = None
    try:
        result["mip_gap"] = float(model.MIPGap)
    except (gp.GurobiError, AttributeError):
        result["mip_gap"] = None

    source_placement = {}
    for e in edges:
        val = int(round(z[e].X))
        if val > 0:
            source_placement[e] = val

    provisioned_tree_units = []
    served_requests = set()
    expected_operation_term = 0.0
    provisioned_unit_count = 0

    for req in requests:
        for t in candidate_trees.get(req.request_id, []):
            var = x[(req.request_id, t.tree_id)]
            units = int(round(var.X))
            if units > 0:
                expected_value = tree_expected_throughput_value(req, t)
                expected_operation_term += expected_value * units
                provisioned_unit_count += units
                provisioned_tree_units.append(
                    {
                        "request_id": req.request_id,
                        "terminals": req.terminals,
                        "tree_id": t.tree_id,
                        "provisioned_units": units,
                        "edges": t.edges,
                        "swap_nodes": t.swap_nodes,
                        "fusion_nodes": t.fusion_nodes,
                        "rho_op": t.rho,
                        "expected_operation_contribution": expected_value * units,
                        "objective_contribution": expected_value * units,
                    }
                )
                served_requests.add(req.request_id)

    result["source_placement"] = source_placement
    result["selected_trees"] = provisioned_tree_units
    result["provisioned_tree_units"] = provisioned_tree_units
    result["x_values"] = {
        key: int(round(var.X))
        for key, var in x.items()
        if int(round(var.X)) > 0
    }
    feasibility_check = validate_integer_provisioning_solution(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_placement=source_placement,
        x_values=result["x_values"],
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=source_cost,
        edge_generation_prob=edge_generation_prob,
    )
    result["feasibility_check"] = feasibility_check
    result["feasible"] = feasibility_check["feasible"]
    result["served_requests"] = sorted(served_requests)
    result["minimum_routing_source_placement"] = dict(sorted(source_placement.items()))
    result["routing_source_placement"] = dict(sorted(source_placement.items()))
    result["ilp_optimized_z_used_budget"] = source_cost * sum(source_placement.values())
    result["deployed_source_count"] = sum(source_placement.values())
    result["deployed_used_budget"] = source_cost * sum(source_placement.values())
    result["ilp_operation_objective_term"] = expected_operation_term
    result["ilp_provisioned_units"] = provisioned_unit_count
    result["throughput_provisioned_units"] = provisioned_unit_count
    candidate_edges = {
        edge
        for trees in candidate_trees.values()
        for tree in trees
        for edge in tree.edges
    }
    result["candidate_edge_count"] = len(candidate_edges)
    result["effective_capacity"] = sum(
        edge_generation_prob.get(edge, 0.0) * max_sources_per_edge
        for edge in candidate_edges
    )
    return result


def status_to_string(status: int) -> str:
    """Convert Gurobi status code to readable string."""
    mapping = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.INTERRUPTED: "INTERRUPTED",
    }
    return mapping.get(status, f"STATUS_{status}")


def generate_random_requests(
    all_nodes: List[Any],
    num_requests: int,
    num_users_per_request: int,
    seed: Optional[int] = None,
) -> List[MultipartiteRequest]:
    """
    Generate multiple multipartite requests.

    Each request contains num_users_per_request terminal nodes.
    """
    if num_users_per_request > len(all_nodes):
        raise ValueError("num_users_per_request exceeds number of nodes.")

    state = random.getstate()
    try:
        set_global_seed(seed)

        request_generator = RequestGenerator(all_nodes)
        requests = []
        for r_id in range(num_requests):
            terminals = request_generator.random_users(k=num_users_per_request)
            requests.append(
                MultipartiteRequest(
                    request_id=r_id,
                    terminals=terminals,
                    weight=1.0,
                    demand=1,
                )
            )
    finally:
        random.setstate(state)

    return requests


def requests_from_user_sets(
    user_sets: Iterable[Iterable[Any]],
    weight: float = 1.0,
    demand: int = 1,
) -> List[MultipartiteRequest]:
    requests = []
    for r_id, terminals in enumerate(user_sets):
        requests.append(
            MultipartiteRequest(
                request_id=r_id,
                terminals=list(terminals),
                weight=float(weight),
                demand=int(demand),
            )
        )
    return requests


def solve_single_slot_ilp_request_batch(
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
    rho_min: float = 0.0,
    max_trees_per_request: Optional[int] = None,
    master_seed: Optional[int] = None,
    time_limit: Optional[float] = None,
    mip_gap: Optional[float] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Solve the ILP for one single-slot multi-request batch.

    This is the ILP-side counterpart of
    run_simulator_single_slot_multi_request.py:
      - request_batch is the same list of requests used by heuristic methods.
      - the source-placement ILP maximizes a provisioning-stage surrogate
        objective using operation-level success probabilities and expected
        edge-capacity constraints.
      - it does not know the actual random realization in the slot; realized
        throughput is measured later by stochastic routing/simulation.
      - all randomized candidate generation and solver randomness are derived
        from master_seed.
    """
    set_global_seed(master_seed)

    topo = Topology(edge_list)
    graph = topo.graph
    requests = requests_from_user_sets(request_batch)

    candidate_seed = derive_seed(master_seed, "ilp", "candidate-trees")
    solver_seed = derive_seed(master_seed, "ilp", "solver")
    gurobi_seed = None if solver_seed is None else int(solver_seed) % GUROBI_SEED_MAX

    candidate_trees = build_candidate_trees_for_requests(
        graph=graph,
        requests=requests,
        k_trees_per_request=k_trees_per_request,
        p_op=p_op,
        q_swap=q_swap,
        q_fus=q_fus,
        q_rem=q_rem,
        rho_min=rho_min,
        weight_attr="length_km",
        seed=candidate_seed,
    )

    if node_memory is None and node_memory_capacity is not None:
        node_memory = {v: int(node_memory_capacity) for v in topo.get_nodes()}

    del max_trees_per_request

    result = solve_joint_source_placement_ilp(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=1,
        p_op=p_op,
        time_limit=time_limit,
        mip_gap=mip_gap,
        solver_seed=gurobi_seed,
        verbose=verbose,
    )

    result["throughput_selected_trees"] = result.get("throughput_provisioned_units", len(result["selected_trees"]))
    result["throughput_covered_requests"] = len(result["served_requests"])
    result["throughput_qbps"] = None
    result["realized_throughput"] = None
    result["provisioning_surrogate_objective"] = result.get("ilp_expected_objective", result["objective"] or 0.0)
    result["ilp_expected_objective"] = result.get("ilp_expected_objective", result["objective"] or 0.0)
    result["ilp_operation_objective_term"] = result.get("ilp_operation_objective_term", 0.0)
    result["ilp_provisioned_units"] = result.get("ilp_provisioned_units", result["throughput_selected_trees"])
    result["deployed_source_count"] = result.get(
        "deployed_source_count",
        sum(result.get("routing_source_placement", {}).values()),
    )
    result["request_batch"] = [list(req) for req in request_batch]
    result["master_seed"] = master_seed
    result["candidate_seed"] = candidate_seed
    result["solver_seed"] = gurobi_seed
    result["candidate_tree_counts"] = {
        req_id: len(trees) for req_id, trees in candidate_trees.items()
    }
    result["request_demand_bound_enabled"] = False
    return result


def print_ilp_result(result: Dict[str, Any]) -> None:
    """Pretty-print ILP result."""
    print("\n" + "=" * 80)
    print("[ILP Result]")
    print("=" * 80)
    print(f"Status: {result['status_name']}")
    print(f"Objective mode: {result.get('objective_mode')}")
    print(f"Operation-weighted provisioning objective: {result.get('ilp_expected_objective', result['objective'])}")
    print(f"Provisioned service units: {result.get('throughput_provisioned_units', 0)}")
    print(f"Requests with provisioned units: {result['served_requests']}")
    print(f"Per-request demand bound enabled: {result.get('request_demand_bound_enabled', False)}")

    print("\n[Source Placement z_e]")
    if not result["source_placement"]:
        print("  No source deployed.")
    else:
        for e, cnt in sorted(result["source_placement"].items()):
            print(f"  edge {e}: {cnt}")

    print("\n[Provisioned Candidate Tree Units]")
    if not result["selected_trees"]:
        print("  No tree service unit provisioned.")
    else:
        for item in result["selected_trees"]:
            print(
                f"  request {item['request_id']}, "
                f"terminals={item['terminals']}, "
                f"tree={item['tree_id']}, "
                f"units={item.get('provisioned_units', 1)}, "
                f"rho_op={item.get('rho_op', item.get('rho', 0.0)):.6e}, "
                f"edges={item['edges']}, "
                f"swap={item['swap_nodes']}, "
                f"fusion={item['fusion_nodes']}"
            )


def demo_small_grid() -> None:
    """
    Small example compatible with the existing 3x3 grid style.
    """
    import single_slot_throughput_sweep_conditions as conditions

    master_seed = conditions.RANDOM_SEED
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
    graph = topo.graph

    # Multiple multipartite requests in one time slot
    requests = generate_random_requests(
        all_nodes=topo.get_nodes(),
        num_requests=3,
        num_users_per_request=3,
        seed=derive_seed(master_seed, "requests"),
    )

    print("\n[Requests]")
    for req in requests:
        print(f"  request {req.request_id}: terminals={req.terminals}")

    candidate_trees = build_candidate_trees_for_requests(
        graph=graph,
        requests=requests,
        k_trees_per_request=8,
        p_op=0.8,
        q_swap=0.95,
        q_fus=0.90,
        rho_min=0.0,
        weight_attr="length_km",
        seed=derive_seed(master_seed, "ilp", "candidate-trees"),
    )

    print("\n[Candidate Trees]")
    for r_id, trees in candidate_trees.items():
        print(f"  request {r_id}: {len(trees)} trees")
        for t in trees:
            print(
                f"    tree {t.tree_id}: rho={t.rho:.6e}, "
                f"edges={t.edges}, swap={t.swap_nodes}, fusion={t.fusion_nodes}"
            )

    # Example memory budget: each node has 4 memories
    node_memory = {v: 4 for v in topo.get_nodes()}

    result = solve_joint_source_placement_ilp(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_budget=8,
        max_sources_per_edge=3,
        node_memory=node_memory,
        source_cost=1,
        allow_multiple_trees_per_request=False,
        time_limit=60,
        mip_gap=0.01,
        solver_seed=derive_seed(master_seed, "ilp", "solver"),
        verbose=True,
    )

    print_ilp_result(result)


if __name__ == "__main__":
    demo_small_grid()
