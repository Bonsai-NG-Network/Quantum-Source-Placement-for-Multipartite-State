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
from steiner_tree_algorithms import gen_multi_steiner_trees
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
    Generate multiple diverse Steiner-like trees using the repository's
    original candidate-tree generator.

    Args:
        graph: physical quantum network.
        terminals: user nodes of one request.
        k_trees: number of candidate trees to attempt.
        weight_attr: kept for API compatibility; the original generator reads
            length_km/weight and calls approximate_steiner_tree internally.
        jitter_ratio: kept for API compatibility.
        overlap_penalty: kept for API compatibility.
        seed: random seed used by the original generator's random module calls.

    Returns:
        List of unique candidate Steiner-like trees.
    """
    terminals = list(terminals)

    if len(terminals) <= 1:
        return []

    state = random.getstate()
    try:
        set_global_seed(seed)
        raw_trees = gen_multi_steiner_trees(graph, terminals, k_trees=k_trees)
    finally:
        random.setstate(state)

    rebuilt_trees = []
    for T in raw_trees:
        H = nx.Graph()
        H.add_nodes_from(T.nodes(data=True))
        for u, v in T.edges():
            if graph.has_edge(u, v):
                H.add_edge(u, v, **graph[u][v])
            else:
                H.add_edge(u, v, **T[u][v])
        rebuilt_trees.append(H)

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
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Solve the joint ILP.

    Variables:
        z_e     integer, number of quantum sources deployed on edge e
        x_{r,t} binary, whether tree t is selected for request r

    Objective:
        maximize the number of selected multipartite state distributions

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

    x = {}
    for req in requests:
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

    for req in requests:
        for t in candidate_trees.get(req.request_id, []):
            var = x[(req.request_id, t.tree_id)]
            obj_terms.append(var)

    model.setObjective(gp.quicksum(obj_terms), GRB.MAXIMIZE)

    # -----------------------------
    # Constraint 1: source budget
    # -----------------------------
    model.addConstr(
        gp.quicksum(source_cost * z[e] for e in edges) <= source_budget,
        name="source_budget",
    )

    # -----------------------------
    # Constraint 2: at most one tree per request
    # or at most D_r trees if redundancy is allowed
    # -----------------------------
    for req in requests:
        model.addConstr(
            gp.quicksum(
                x[(req.request_id, t.tree_id)]
                for t in candidate_trees.get(req.request_id, [])
            ) <= demand_by_request[req.request_id],
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
                        "objective_contribution": 1.0,
                    }
                )
                served_requests.add(req.request_id)
                for e in t.edges:
                    routing_source_placement[e] = routing_source_placement.get(e, 0) + 1

    result["source_placement"] = source_placement
    result["routing_source_placement"] = routing_source_placement
    result["selected_trees"] = selected_trees
    result["served_requests"] = sorted(served_requests)

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

    result = solve_joint_source_placement_ilp(
        graph=graph,
        requests=requests,
        candidate_trees=candidate_trees,
        source_budget=source_budget,
        max_sources_per_edge=max_sources_per_edge,
        node_memory=node_memory,
        source_cost=1,
        allow_multiple_trees_per_request=False,
        time_limit=time_limit,
        mip_gap=mip_gap,
        solver_seed=gurobi_seed,
        verbose=verbose,
    )

    result["throughput_qbps"] = result["objective"] or 0.0
    result["throughput_expected_qbps"] = result["throughput_qbps"]
    result["throughput_selected_trees"] = len(result["selected_trees"])
    result["request_batch"] = [list(req) for req in request_batch]
    result["master_seed"] = master_seed
    result["candidate_seed"] = candidate_seed
    result["solver_seed"] = gurobi_seed
    result["candidate_tree_counts"] = {
        req_id: len(trees) for req_id, trees in candidate_trees.items()
    }
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
    master_seed = 1
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
