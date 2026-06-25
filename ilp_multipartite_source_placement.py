"""
ILP-based Joint Quantum Source Placement and Multipartite Tree Selection.

This module implements the journal-extension ILP:

    max sum_{r in R} sum_{t in T_r} x_{r,t}

subject to:
    source budget
    per-edge source cap
    at most one selected tree per request
    edge-source capacity coupling
    node-memory capacity
    binary/integer domains

It is designed to be compatible with the existing repository:
    - network_topology.py
    - network_request.py
    - steiner_tree_algorithms.py

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
    Secondary reward for selecting one candidate tree.

    The main ILP objective gives first priority to covering distinct requests.
    This term is then used to choose redundant trees and prefer higher-quality
    candidate trees.
    """
    return 1.0 + float(tree.rho)


def served_request_priority(candidate_trees: Dict[int, List[CandidateTree]]) -> float:
    """Return the fixed lexicographic weight for serving one additional request."""
    try:
        import single_slot_throughput_sweep_conditions as conditions

        return float(conditions.ILP_REQUEST_PRIORITY)
    except (ImportError, AttributeError):
        return 1000.0


def z_reward_decay(max_sources_per_edge: int) -> List[float]:
    """Return diminishing reward multipliers for incremental sources on one edge."""
    try:
        import single_slot_throughput_sweep_conditions as conditions

        values = [float(value) for value in conditions.ILP_Z_REWARD_DECAY]
    except (ImportError, AttributeError):
        values = [1.0, 0.7, 0.4, 0.2, 0.1, 0.05, 0.025]

    if not values:
        values = [1.0]

    while len(values) < int(max_sources_per_edge):
        values.append(values[-1] * 0.5)

    return values[: int(max_sources_per_edge)]


def allocate_redundant_source_placement(
    selected_trees: List[Dict[str, Any]],
    source_budget: int,
    max_sources_per_edge: int,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    candidate_trees: Optional[Dict[int, List[CandidateTree]]] = None,
    fallback_edges: Optional[Iterable[Edge]] = None,
    enforce_node_memory_for_redundancy: bool = True,
) -> Dict[str, Any]:
    """
    Convert selected request trees into a routing source placement and spend
    remaining budget as redundant sources on request-relevant candidate edges.

    The initial placement puts one source on every selected tree edge. Extra
    sources are added round-robin to the lowest-load candidate edges, preferring
    edges that appear in higher-quality candidate trees. Extra sources obey the
    same per-edge capacity and, when node_memory is provided, node-memory
    capacity used by the ILP model.
    """
    edge_load: Dict[Edge, int] = {}
    edge_score: Dict[Edge, float] = {}
    candidate_edge_set = set()

    for tree in selected_trees:
        rho = float(tree.get("rho", 1.0) or 0.0)
        for edge in tree.get("edges", []):
            edge_key = norm_edge(edge[0], edge[1])
            candidate_edge_set.add(edge_key)
            edge_load[edge_key] = edge_load.get(edge_key, 0) + 1
            edge_score[edge_key] = edge_score.get(edge_key, 0.0) + rho

    if candidate_trees is not None:
        for trees in candidate_trees.values():
            for tree in trees:
                for edge in tree.edges:
                    edge_key = norm_edge(edge[0], edge[1])
                    candidate_edge_set.add(edge_key)
                    edge_score[edge_key] = edge_score.get(edge_key, 0.0) + float(tree.rho)

    if fallback_edges is not None:
        for edge in fallback_edges:
            edge_key = norm_edge(edge[0], edge[1])
            candidate_edge_set.add(edge_key)
            edge_score.setdefault(edge_key, 0.0)

    node_load: Dict[Any, int] = {}
    for edge, count in edge_load.items():
        u, v = edge
        node_load[u] = node_load.get(u, 0) + count
        node_load[v] = node_load.get(v, 0) + count

    def has_node_capacity(edge: Edge) -> bool:
        if node_memory is None or not enforce_node_memory_for_redundancy:
            return True
        u, v = edge
        return (
            node_load.get(u, 0) + 1 <= int(node_memory.get(u, 0))
            and node_load.get(v, 0) + 1 <= int(node_memory.get(v, 0))
        )

    used_budget = source_cost * sum(edge_load.values())
    while used_budget + source_cost <= source_budget:
        candidates = [
            edge
            for edge in candidate_edge_set
            if edge_load.get(edge, 0) < max_sources_per_edge and has_node_capacity(edge)
        ]
        if not candidates:
            break

        chosen = min(
            candidates,
            key=lambda edge: (
                edge_load.get(edge, 0),
                -edge_score.get(edge, 0.0),
                edge[0],
                edge[1],
            ),
        )
        edge_load[chosen] = edge_load.get(chosen, 0) + 1
        u, v = chosen
        node_load[u] = node_load.get(u, 0) + 1
        node_load[v] = node_load.get(v, 0) + 1
        used_budget += source_cost

    return {
        "routing_source_placement": dict(sorted(edge_load.items())),
        "used_budget": used_budget,
        "memory_load": dict(sorted(node_load.items())),
        "candidate_edge_count": len(candidate_edge_set),
        "effective_capacity": len(candidate_edge_set) * max_sources_per_edge,
    }


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


def compute_tree_memory(
    tree: nx.Graph,
    terminals: Iterable[Any],
    memory_model: str = "degree",
) -> Dict[Any, int]:
    """
    Compute node memory consumption of one tree.

    First journal-version model:
        m_{v,t} = degree of v in tree

    This corresponds to one local memory qubit per incident entanglement link.
    """
    if memory_model != "degree":
        raise ValueError("Currently only memory_model='degree' is implemented.")

    memory = {v: int(tree.degree(v)) for v in tree.nodes()}
    return memory


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
    Compute tree-level success probability:

        rho_t =
            prod_{e in E_t} p_e
            * prod_{v in V_swap} q_v^swap
            * prod_{v in V_fus} q_v^fus

    q_swap and q_fus can be constants first.
    Later they can be dictionaries if node-dependent probabilities are needed.
    """
    rho = 1.0

    for u, v, data in tree.edges(data=True):
        length_km = float(data.get(weight_attr, data.get("weight", 1.0)))
        rho *= edge_success_prob(
            length_km=length_km,
            p_op=p_op,
            loss_coef_db_per_km=loss_coef_db_per_km,
        )

    for node in swap_nodes:
        rho *= probability_for(node, q_swap)

    for node in fusion_nodes:
        rho *= probability_for(node, q_fus)

    for node in removal_nodes or []:
        rho *= probability_for(node, q_rem)

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

            memory = compute_tree_memory(
                T,
                terminals=req.terminals,
                memory_model="degree",
            )

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
                    memory=memory,
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
    Build request-aware fallback edges from shortest paths between batch users.

    These edges are used only for redundant source placement after tree
    selection. They prevent unused budget without falling back to topology-wide
    all-edge placement.
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


def build_edge_redundancy_rewards(
    graph: nx.Graph,
    candidate_trees: Dict[int, List[CandidateTree]],
    fallback_edges: Optional[Iterable[Edge]] = None,
) -> Dict[Edge, float]:
    """
    Build a normalized request-aware reward for redundant source placement.

    The selected-tree terms still define the main ILP objective. This reward is
    only a small tie-breaker that tells the z_e variables where to spend
    remaining budget for routing.
    """
    rewards = {norm_edge(u, v): 1e-3 for u, v in graph.edges()}

    for trees in candidate_trees.values():
        for tree in trees:
            tree_reward = tree_objective_value(tree)
            for edge in tree.edges:
                edge_key = norm_edge(edge[0], edge[1])
                rewards[edge_key] = rewards.get(edge_key, 1e-3) + tree_reward

    if fallback_edges is not None:
        for edge in fallback_edges:
            edge_key = norm_edge(edge[0], edge[1])
            rewards[edge_key] = rewards.get(edge_key, 1e-3) + 0.5

    max_reward = max(rewards.values(), default=1.0)
    if max_reward <= 0:
        return rewards
    return {edge: value / max_reward for edge, value in rewards.items()}


def solve_joint_source_placement_ilp(
    graph: nx.Graph,
    requests: List[MultipartiteRequest],
    candidate_trees: Dict[int, List[CandidateTree]],
    source_budget: int,
    max_sources_per_edge: int = 5,
    node_memory: Optional[Dict[Any, int]] = None,
    source_cost: int = 1,
    allow_multiple_trees_per_request: bool = False,
    demand_per_request: int = 1,
    request_demands: Optional[Dict[int, int]] = None,
    time_limit: Optional[float] = None,
    mip_gap: Optional[float] = None,
    solver_seed: Optional[int] = None,
    edge_redundancy_weight: float = 0.05,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Solve the joint ILP.

    Variables:
        z_e     integer, number of quantum sources deployed on edge e
        x_{r,t} binary, whether tree t is selected for request r

    Objective:
        maximize the number of covered requests first, then select redundant
        candidate trees for covered requests, using rho_t as a tie-breaker.

    Demand handling:
        By default, each request can select at most one tree.
        If allow_multiple_trees_per_request=True, request r can select up to
        D_r trees, where D_r is read from request_demands[r] if provided,
        otherwise from req.demand, otherwise from demand_per_request.

    Returns:
        dict containing source placement, selected trees, objective value,
        served requests, and Gurobi status.
    """
    edges = sorted(norm_edge(u, v) for u, v in graph.edges())
    nodes = sorted(graph.nodes())

    if node_memory is None:
        # Default memory capacity: large enough not to bind.
        node_memory = {v: 10**6 for v in nodes}

    demand_by_request = {}
    for req in requests:
        if request_demands is not None and req.request_id in request_demands:
            demand = int(request_demands[req.request_id])
        elif allow_multiple_trees_per_request:
            req_demand = int(getattr(req, "demand", 1))
            demand = req_demand if req_demand != 1 else int(demand_per_request)
        else:
            demand = 1

        if demand < 1:
            raise ValueError(f"Request demand must be >= 1 for request {req.request_id}.")

        demand_by_request[req.request_id] = demand

    model = gp.Model("joint_source_placement_multipartite_tree_selection")

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
    z_increment = {
        (e, k): model.addVar(
            vtype=GRB.BINARY,
            name=f"u_{e[0]}_{e[1]}_{k + 1}",
        )
        for e in edges
        for k in range(max_sources_per_edge)
    }

    x = {}
    y = {}
    for req in requests:
        y[req.request_id] = model.addVar(
            vtype=GRB.BINARY,
            name=f"y_r{req.request_id}",
        )
        for t in candidate_trees.get(req.request_id, []):
            x[(req.request_id, t.tree_id)] = model.addVar(
                vtype=GRB.BINARY,
                name=f"x_r{req.request_id}_t{t.tree_id}",
            )

    model.update()

    # -----------------------------
    # Objective
    # -----------------------------
    obj_terms = []
    request_priority = served_request_priority(candidate_trees)
    fallback_edges = build_batch_user_path_edges(graph, requests)
    edge_redundancy_rewards = build_edge_redundancy_rewards(
        graph=graph,
        candidate_trees=candidate_trees,
        fallback_edges=fallback_edges,
    )
    z_decay = z_reward_decay(max_sources_per_edge)

    for req in requests:
        obj_terms.append(request_priority * y[req.request_id])

    for req in requests:
        for t in candidate_trees.get(req.request_id, []):
            var = x[(req.request_id, t.tree_id)]
            obj_terms.append(tree_objective_value(t) * var)

    if edge_redundancy_weight > 0:
        for e in edges:
            edge_reward = edge_redundancy_rewards.get(e, 0.0)
            for k, decay in enumerate(z_decay):
                obj_terms.append(
                    edge_redundancy_weight
                    * edge_reward
                    * float(decay)
                    * z_increment[(e, k)]
                )

    model.setObjective(gp.quicksum(obj_terms), GRB.MAXIMIZE)

    # -----------------------------
    # Constraint 1: source budget
    # -----------------------------
    model.addConstr(
        gp.quicksum(source_cost * z[e] for e in edges) <= source_budget,
        name="source_budget",
    )

    for e in edges:
        model.addConstr(
            z[e] == gp.quicksum(z_increment[(e, k)] for k in range(max_sources_per_edge)),
            name=f"source_increment_sum_{e[0]}_{e[1]}",
        )
        for k in range(1, max_sources_per_edge):
            model.addConstr(
                z_increment[(e, k)] <= z_increment[(e, k - 1)],
                name=f"source_increment_order_{e[0]}_{e[1]}_{k + 1}",
            )

    # -----------------------------
    # Constraint 2: request coverage and redundant tree limit
    # -----------------------------
    for req in requests:
        selected_for_request = gp.quicksum(
            x[(req.request_id, t.tree_id)]
            for t in candidate_trees.get(req.request_id, [])
        )
        model.addConstr(
            selected_for_request >= y[req.request_id],
            name=f"request_coverage_lower_r{req.request_id}",
        )
        model.addConstr(
            selected_for_request <= demand_by_request[req.request_id] * y[req.request_id],
            name=f"request_tree_limit_r{req.request_id}",
        )

    # -----------------------------
    # Constraint 3: edge-source capacity
    #
    # sum_{r,t} a_{e,t} x_{r,t} <= z_e
    # -----------------------------
    for e in edges:
        model.addConstr(
            gp.quicksum(
                x[(req.request_id, t.tree_id)]
                for req in requests
                for t in candidate_trees.get(req.request_id, [])
                if e in t.edges
            ) <= z[e],
            name=f"edge_source_capacity_{e[0]}_{e[1]}",
        )

    # -----------------------------
    # Constraint 4: node-memory capacity
    #
    # sum_{r,t} m_{v,t} x_{r,t} <= M_v
    # sum_{e incident to v} z_e <= M_v
    # -----------------------------
    for v in nodes:
        model.addConstr(
            gp.quicksum(
                t.memory.get(v, 0) * x[(req.request_id, t.tree_id)]
                for req in requests
                for t in candidate_trees.get(req.request_id, [])
            ) <= int(node_memory.get(v, 0)),
            name=f"node_memory_{v}",
        )
        model.addConstr(
            gp.quicksum(
                z[e]
                for e in edges
                if v in e
            ) <= int(node_memory.get(v, 0)),
            name=f"node_memory_source_placement_{v}",
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
        "objective": None,
        "source_placement": {},
        "routing_source_placement": {},
        "selected_trees": [],
        "served_requests": [],
        "covered_requests": [],
        "request_demands": demand_by_request,
        "solver_seed": solver_seed,
        "model": model,
    }

    if status not in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
        return result

    if model.SolCount == 0:
        return result

    result["objective"] = float(model.ObjVal)

    source_placement = {}
    for e in edges:
        val = int(round(z[e].X))
        if val > 0:
            source_placement[e] = val

    selected_trees = []
    served_requests = set()
    routing_source_placement = {}

    for req in requests:
        for t in candidate_trees.get(req.request_id, []):
            var = x[(req.request_id, t.tree_id)]
            if var.X > 0.5:
                selected_trees.append(
                    {
                        "request_id": req.request_id,
                        "terminals": req.terminals,
                        "tree_id": t.tree_id,
                        "edges": t.edges,
                        "swap_nodes": t.swap_nodes,
                        "fusion_nodes": t.fusion_nodes,
                        "memory": t.memory,
                        "rho": t.rho,
                        "objective_contribution": tree_objective_value(t),
                    }
                )
                served_requests.add(req.request_id)
                for e in t.edges:
                    routing_source_placement[e] = routing_source_placement.get(e, 0) + 1

    result["source_placement"] = source_placement
    result["selected_trees"] = selected_trees
    result["served_requests"] = sorted(served_requests)
    result["covered_requests"] = [
        req.request_id for req in requests if y[req.request_id].X > 0.5
    ]
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
    result["minimum_routing_source_placement"] = dict(sorted(routing_source_placement.items()))
    result["routing_source_placement"] = dict(sorted(source_placement.items()))
    result["ilp_optimized_z_used_budget"] = source_cost * sum(source_placement.values())
    result["redundant_used_budget"] = redundant["used_budget"]
    result["redundant_routing_source_placement"] = redundant["routing_source_placement"]
    result["redundant_memory_load"] = redundant["memory_load"]
    result["redundant_enforce_node_memory"] = True
    result["candidate_edge_count"] = len(edge_redundancy_rewards)
    result["effective_capacity"] = len(edge_redundancy_rewards) * max_sources_per_edge
    result["edge_redundancy_weight"] = edge_redundancy_weight

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
    edge_redundancy_weight: Optional[float] = None,
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
      - objective is throughput per slot, sum x_{r,t}.
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

    if max_trees_per_request is None:
        try:
            import single_slot_throughput_sweep_conditions as conditions

            max_trees_per_request = int(conditions.ILP_MAX_TREES_PER_REQUEST)
        except (ImportError, AttributeError):
            max_trees_per_request = int(k_trees_per_request)

    if edge_redundancy_weight is None:
        try:
            import single_slot_throughput_sweep_conditions as conditions

            edge_redundancy_weight = float(conditions.ILP_EDGE_REDUNDANCY_WEIGHT)
        except (ImportError, AttributeError):
            edge_redundancy_weight = 0.05

    result = solve_joint_source_placement_ilp(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=1,
        allow_multiple_trees_per_request=True,
        demand_per_request=max(1, int(max_trees_per_request)),
        time_limit=time_limit,
        mip_gap=mip_gap,
        solver_seed=gurobi_seed,
        edge_redundancy_weight=float(edge_redundancy_weight),
        verbose=verbose,
    )

    result["throughput_selected_trees"] = len(result["selected_trees"])
    result["throughput_covered_requests"] = len(result.get("covered_requests", result["served_requests"]))
    result["throughput_qbps"] = result["throughput_covered_requests"]
    result["throughput_expected_objective"] = result["objective"] or 0.0
    result["request_batch"] = [list(req) for req in request_batch]
    result["master_seed"] = master_seed
    result["candidate_seed"] = candidate_seed
    result["solver_seed"] = gurobi_seed
    result["candidate_tree_counts"] = {
        req_id: len(trees) for req_id, trees in candidate_trees.items()
    }
    result["max_trees_per_request"] = max(1, int(max_trees_per_request))
    result["configured_edge_redundancy_weight"] = float(edge_redundancy_weight)
    return result


def print_ilp_result(result: Dict[str, Any]) -> None:
    """Pretty-print ILP result."""
    print("\n" + "=" * 80)
    print("[ILP Result]")
    print("=" * 80)
    print(f"Status: {result['status_name']}")
    print(f"Objective: {result['objective']}")
    print(f"Served requests: {result['served_requests']}")
    print(f"Request demands D_r: {result.get('request_demands', {})}")

    print("\n[Source Placement z_e]")
    if not result["source_placement"]:
        print("  No source deployed.")
    else:
        for e, cnt in sorted(result["source_placement"].items()):
            print(f"  edge {e}: {cnt}")

    print("\n[Selected Candidate Trees]")
    if not result["selected_trees"]:
        print("  No tree selected.")
    else:
        for item in result["selected_trees"]:
            print(
                f"  request {item['request_id']}, "
                f"terminals={item['terminals']}, "
                f"tree={item['tree_id']}, "
                f"rho={item['rho']:.6e}, "
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
