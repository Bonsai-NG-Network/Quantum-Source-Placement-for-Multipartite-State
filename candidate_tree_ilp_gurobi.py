"""
Candidate-tree-based ILP for multipartite state distribution.

Main idea
---------
1. Fix candidate placements.
2. For each placement and each given scenario:
   - generate feasible multipartite candidate trees for each request
   - precompute edge usage, memory usage, and success probabilities
3. Filter out low-probability trees.
4. Build a tree-selection ILP with Gurobi.

This file intentionally separates:
- offline preprocessing
- ILP data construction
- Gurobi model building

You can replace the placeholder tree-generation logic with your own heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Union
from collections import defaultdict

import networkx as nx
import gurobipy as gp
from gurobipy import GRB


Node = Union[int, str]
Edge = Tuple[Node, Node]
RequestId = Union[int, str]
PlacementId = Union[int, str]
ScenarioId = Union[int, str]
TreeId = int


@dataclass(frozen=True)
class Request:
    req_id: RequestId
    terminals: Set[Node]
    weight: float = 1.0


@dataclass(frozen=True)
class Placement:
    placement_id: PlacementId
    x_e: Dict[Edge, int]


@dataclass(frozen=True)
class Scenario:
    scenario_id: ScenarioId
    placement_id: PlacementId
    probability: float
    available_edges: Set[Edge]


@dataclass
class CandidateTree:
    tree_id: TreeId
    request_id: RequestId
    placement_id: PlacementId
    scenario_id: ScenarioId
    edges: Set[Edge]
    swap_nodes: Set[Node] = field(default_factory=set)
    fusion_nodes: Set[Node] = field(default_factory=set)
    edge_incidence: Dict[Edge, int] = field(default_factory=dict)
    memory_usage: Dict[Node, int] = field(default_factory=dict)
    rho_gen: float = 1.0
    rho_swap: float = 1.0
    rho_fus: float = 1.0

    @property
    def rho(self) -> float:
        return self.rho_gen * self.rho_swap * self.rho_fus


def normalize_edge(u: Node, v: Node) -> Edge:
    return (u, v) if str(u) <= str(v) else (v, u)


def all_graph_edges(G: nx.Graph) -> List[Edge]:
    return [normalize_edge(u, v) for u, v in G.edges()]


def get_edge_probability(
    e: Edge,
    placement: Placement,
    one_source_success_prob: Dict[Edge, float],
) -> float:
    x = placement.x_e.get(e, 0)
    p = one_source_success_prob.get(e, 0.0)
    if x <= 0 or p <= 0:
        return 0.0
    return 1.0 - (1.0 - p) ** x


def choose_candidate_placements(
    heuristic_output: List[Dict[Edge, int]]
) -> List[Placement]:
    placements: List[Placement] = []
    for i, x_e in enumerate(heuristic_output):
        placements.append(Placement(placement_id=f"p{i}", x_e=x_e))
    return placements


def generate_candidate_trees_for_request(
    G_scenario: nx.Graph,
    request: Request,
    placement_id: PlacementId,
    scenario_id: ScenarioId,
    tree_id_start: int,
    max_trees_per_request: int = 10,
) -> List[CandidateTree]:
    terminals = list(request.terminals)
    if len(terminals) < 2:
        return []

    trees: List[CandidateTree] = []
    seen_edge_sets: Set[frozenset[Edge]] = set()
    candidate_roots = terminals[: min(len(terminals), max_trees_per_request)]
    next_tree_id = tree_id_start

    for root in candidate_roots:
        sub_edges: Set[Edge] = set()
        feasible = True

        for t in terminals:
            if t == root:
                continue
            try:
                path = nx.shortest_path(G_scenario, source=root, target=t)
            except nx.NetworkXNoPath:
                feasible = False
                break
            for u, v in zip(path[:-1], path[1:]):
                sub_edges.add(normalize_edge(u, v))

        if not feasible or not sub_edges:
            continue

        H = nx.Graph()
        H.add_edges_from(sub_edges)
        T_nx = nx.minimum_spanning_tree(H)
        edge_set = frozenset(normalize_edge(u, v) for u, v in T_nx.edges())

        if edge_set in seen_edge_sets:
            continue
        seen_edge_sets.add(edge_set)

        swap_nodes: Set[Node] = set()
        fusion_nodes: Set[Node] = set()
        for v, deg in T_nx.degree():
            if v in request.terminals:
                continue
            if deg == 2:
                swap_nodes.add(v)
            elif deg >= 3:
                fusion_nodes.add(v)

        tree = CandidateTree(
            tree_id=next_tree_id,
            request_id=request.req_id,
            placement_id=placement_id,
            scenario_id=scenario_id,
            edges=set(edge_set),
            swap_nodes=swap_nodes,
            fusion_nodes=fusion_nodes,
        )
        trees.append(tree)
        next_tree_id += 1

        if len(trees) >= max_trees_per_request:
            break

    return trees


def precompute_tree_parameters(
    tree: CandidateTree,
    request_lookup: Dict[RequestId, Request],
    placement_lookup: Dict[PlacementId, Placement],
    one_source_success_prob: Dict[Edge, float],
    swap_success_prob: Dict[Node, float],
    fusion_success_prob: Dict[Node, float],
    terminal_memory_cost: int = 1,
    swap_memory_cost: int = 2,
    fusion_memory_cost: int = 3,
) -> None:
    request = request_lookup[tree.request_id]
    placement = placement_lookup[tree.placement_id]

    tree.edge_incidence = {e: 1 for e in tree.edges}

    rho_gen = 1.0
    for e in tree.edges:
        rho_gen *= get_edge_probability(e, placement, one_source_success_prob)
    tree.rho_gen = rho_gen

    rho_swap = 1.0
    for v in tree.swap_nodes:
        rho_swap *= swap_success_prob.get(v, 1.0)
    tree.rho_swap = rho_swap

    rho_fus = 1.0
    for v in tree.fusion_nodes:
        rho_fus *= fusion_success_prob.get(v, 1.0)
    tree.rho_fus = rho_fus

    memory_usage: Dict[Node, int] = defaultdict(int)
    for v in request.terminals:
        deg = sum(1 for e in tree.edges if v in e)
        if deg > 0:
            memory_usage[v] += terminal_memory_cost
    for v in tree.swap_nodes:
        memory_usage[v] += swap_memory_cost
    for v in tree.fusion_nodes:
        memory_usage[v] += fusion_memory_cost

    tree.memory_usage = dict(memory_usage)


def build_candidate_tree_pool(
    G: nx.Graph,
    requests: List[Request],
    placements: List[Placement],
    scenarios_by_placement: Dict[PlacementId, List[Scenario]],
    one_source_success_prob: Dict[Edge, float],
    swap_success_prob: Dict[Node, float],
    fusion_success_prob: Dict[Node, float],
    rho_min: float,
    max_trees_per_request: int = 10,
) -> List[CandidateTree]:
    request_lookup = {r.req_id: r for r in requests}
    placement_lookup = {p.placement_id: p for p in placements}

    tree_pool: List[CandidateTree] = []
    next_tree_id = 0

    for p in placements:
        for scenario in scenarios_by_placement.get(p.placement_id, []):
            G_s = nx.Graph()
            G_s.add_nodes_from(G.nodes())
            G_s.add_edges_from(list(scenario.available_edges))

            for req in requests:
                trees = generate_candidate_trees_for_request(
                    G_scenario=G_s,
                    request=req,
                    placement_id=p.placement_id,
                    scenario_id=scenario.scenario_id,
                    tree_id_start=next_tree_id,
                    max_trees_per_request=max_trees_per_request,
                )
                next_tree_id += len(trees)

                for tree in trees:
                    precompute_tree_parameters(
                        tree=tree,
                        request_lookup=request_lookup,
                        placement_lookup=placement_lookup,
                        one_source_success_prob=one_source_success_prob,
                        swap_success_prob=swap_success_prob,
                        fusion_success_prob=fusion_success_prob,
                    )
                    if tree.rho >= rho_min:
                        tree_pool.append(tree)

    return tree_pool


@dataclass
class ILPData:
    trees: List[CandidateTree]
    requests: List[Request]
    placements: List[Placement]
    scenarios_by_placement: Dict[PlacementId, List[Scenario]]
    edge_capacity: Dict[Edge, int]
    node_memory_budget: Dict[Node, int]
    trees_by_req_place_scen: Dict[Tuple[RequestId, PlacementId, ScenarioId], List[CandidateTree]] = field(default_factory=dict)

    def build_indices(self) -> None:
        idx: Dict[Tuple[RequestId, PlacementId, ScenarioId], List[CandidateTree]] = defaultdict(list)
        for t in self.trees:
            idx[(t.request_id, t.placement_id, t.scenario_id)].append(t)
        self.trees_by_req_place_scen = dict(idx)


def build_candidate_tree_ilp(
    ilp_data: ILPData,
    use_global_placement_selection: bool = False,
    model_name: str = "candidate_tree_ilp",
) -> gp.Model:
    ilp_data.build_indices()

    req_lookup = {r.req_id: r for r in ilp_data.requests}
    scen_lookup = {
        (s.placement_id, s.scenario_id): s
        for plist in ilp_data.scenarios_by_placement.values()
        for s in plist
    }

    model = gp.Model(model_name)

    x = {
        t.tree_id: model.addVar(vtype=GRB.BINARY, name=f"x[{t.tree_id}]")
        for t in ilp_data.trees
    }

    y = {}
    if use_global_placement_selection:
        y = {
            p.placement_id: model.addVar(vtype=GRB.BINARY, name=f"y[{p.placement_id}]")
            for p in ilp_data.placements
        }

    model.update()

    model.setObjective(
        gp.quicksum(
            req_lookup[t.request_id].weight
            * scen_lookup[(t.placement_id, t.scenario_id)].probability
            * t.rho
            * x[t.tree_id]
            for t in ilp_data.trees
        ),
        GRB.MAXIMIZE,
    )

    for (req_id, placement_id, scenario_id), trees in ilp_data.trees_by_req_place_scen.items():
        model.addConstr(
            gp.quicksum(x[t.tree_id] for t in trees) <= 1,
            name=f"one_tree[{req_id},{placement_id},{scenario_id}]"
        )

    for e in ilp_data.edge_capacity:
        model.addConstr(
            gp.quicksum(t.edge_incidence.get(e, 0) * x[t.tree_id] for t in ilp_data.trees)
            <= ilp_data.edge_capacity[e],
            name=f"edge_cap[{e}]"
        )

    for v in ilp_data.node_memory_budget:
        model.addConstr(
            gp.quicksum(t.memory_usage.get(v, 0) * x[t.tree_id] for t in ilp_data.trees)
            <= ilp_data.node_memory_budget[v],
            name=f"mem[{v}]"
        )

    if use_global_placement_selection:
        model.addConstr(
            gp.quicksum(y[p.placement_id] for p in ilp_data.placements) == 1,
            name="one_placement"
        )
        for t in ilp_data.trees:
            model.addConstr(
                x[t.tree_id] <= y[t.placement_id],
                name=f"place_link[{t.tree_id}]"
            )

    return model


def extract_selected_trees(model: gp.Model, trees: List[CandidateTree]) -> List[CandidateTree]:
    name_to_tree = {f"x[{t.tree_id}]": t for t in trees}
    selected: List[CandidateTree] = []
    for var in model.getVars():
        if var.varName.startswith("x[") and var.X > 0.5:
            selected.append(name_to_tree[var.varName])
    return selected


def demo() -> None:
    G = nx.Graph()
    G.add_edges_from([
        ("A", "B"), ("B", "C"), ("C", "D"),
        ("A", "E"), ("E", "F"), ("F", "D"),
        ("B", "E"), ("C", "F"),
    ])
    edges = all_graph_edges(G)

    requests = [
        Request(req_id="r1", terminals={"A", "C", "D"}, weight=1.0),
        Request(req_id="r2", terminals={"A", "E", "F"}, weight=1.2),
    ]

    heuristic_output = [
        {e: 1 for e in edges},
        {e: (2 if e in {normalize_edge("A", "B"), normalize_edge("B", "C")} else 1) for e in edges},
    ]
    placements = choose_candidate_placements(heuristic_output)

    scenarios_by_placement: Dict[PlacementId, List[Scenario]] = {
        "p0": [
            Scenario("w0", "p0", 0.5, set(edges)),
            Scenario("w1", "p0", 0.5, {e for e in edges if e != normalize_edge("B", "C")}),
        ],
        "p1": [
            Scenario("w0", "p1", 0.6, set(edges)),
            Scenario("w1", "p1", 0.4, {e for e in edges if e != normalize_edge("C", "D")}),
        ],
    }

    one_source_success_prob = {e: 0.85 for e in edges}
    swap_success_prob = {v: 0.95 for v in G.nodes()}
    fusion_success_prob = {v: 0.90 for v in G.nodes()}

    tree_pool = build_candidate_tree_pool(
        G=G,
        requests=requests,
        placements=placements,
        scenarios_by_placement=scenarios_by_placement,
        one_source_success_prob=one_source_success_prob,
        swap_success_prob=swap_success_prob,
        fusion_success_prob=fusion_success_prob,
        rho_min=0.05,
        max_trees_per_request=5,
    )

    print(f"Generated {len(tree_pool)} candidate trees after filtering.")

    ilp_data = ILPData(
        trees=tree_pool,
        requests=requests,
        placements=placements,
        scenarios_by_placement=scenarios_by_placement,
        edge_capacity={e: 1 for e in edges},
        node_memory_budget={v: 4 for v in G.nodes()},
    )

    model = build_candidate_tree_ilp(
        ilp_data=ilp_data,
        use_global_placement_selection=True,
    )
    model.Params.OutputFlag = 1
    model.optimize()

    if model.status == GRB.OPTIMAL:
        print(f"Optimal objective = {model.objVal:.6f}")
        selected = extract_selected_trees(model, tree_pool)
        for t in selected:
            print(
                f"Selected tree {t.tree_id}: "
                f"req={t.request_id}, placement={t.placement_id}, scenario={t.scenario_id}, "
                f"rho={t.rho:.6f}, edges={sorted(list(t.edges), key=str)}"
            )
    else:
        print(f"Model status: {model.status}")


if __name__ == "__main__":
    demo()
