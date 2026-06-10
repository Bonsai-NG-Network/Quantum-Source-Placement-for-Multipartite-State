from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import networkx as nx


ProbabilitySpec = Union[float, Dict[Any, float], None]
Edge = Tuple[Any, Any]


@dataclass
class TreeOperationPlan:
    tree: nx.Graph
    users: List[Any]
    swap_nodes: List[Any]
    fusion_nodes: List[Any]
    candidate_removal_nodes: List[Any]
    reduced_tree: nx.Graph
    reduced_edge_paths: Dict[Edge, List[Any]]
    rho: float


def norm_edge(u: Any, v: Any) -> Edge:
    return (u, v) if u <= v else (v, u)


def probability_for(item: Any, spec: ProbabilitySpec, default: float = 1.0) -> float:
    if spec is None:
        return default
    if isinstance(spec, dict):
        return float(spec.get(item, default))
    return float(spec)


def edge_success_prob(
    length_km: float,
    p_op: float = 0.8,
    loss_coef_db_per_km: float = 0.2,
) -> float:
    transmittance = 10.0 ** (-(loss_coef_db_per_km * length_km) / 10.0)
    return float(p_op * transmittance)


def identify_swap_nodes(tree: nx.Graph, users: Iterable[Any]) -> List[Any]:
    user_set = set(users)
    return sorted(v for v in tree.nodes() if v not in user_set and tree.degree(v) == 2)


def build_reduced_tree(
    tree: nx.Graph,
    users: Iterable[Any],
    retained_nodes: Optional[Iterable[Any]] = None,
) -> Tuple[nx.Graph, Dict[Edge, List[Any]]]:
    user_set = set(users)
    retained_extra = set(retained_nodes or [])
    retained = {
        node
        for node in tree.nodes()
        if node in user_set or node in retained_extra or tree.degree(node) != 2
    }

    reduced = nx.Graph()
    for node in retained:
        reduced.add_node(node, **tree.nodes[node])

    edge_paths: Dict[Edge, List[Any]] = {}
    visited_directed = set()

    for start in retained:
        for neighbor in tree.neighbors(start):
            directed = (start, neighbor)
            if directed in visited_directed:
                continue

            path = [start, neighbor]
            prev = start
            cur = neighbor

            while cur not in retained:
                next_nodes = [n for n in tree.neighbors(cur) if n != prev]
                if not next_nodes:
                    break
                prev, cur = cur, next_nodes[0]
                path.append(cur)

            if cur not in retained or cur == start:
                continue

            for a, b in zip(path, path[1:]):
                visited_directed.add((a, b))
                visited_directed.add((b, a))

            edge = norm_edge(start, cur)
            if edge not in edge_paths:
                edge_paths[edge] = path
                reduced.add_edge(start, cur)

    return reduced, edge_paths


def identify_fusion_nodes(reduced_tree: nx.Graph) -> List[Any]:
    return sorted(v for v in reduced_tree.nodes() if reduced_tree.degree(v) >= 2)


def build_tree_from_paths(paths: Dict[Any, List[Any]]) -> nx.Graph:
    tree = nx.Graph()
    for path in paths.values():
        for u, v in zip(path, path[1:]):
            tree.add_edge(u, v)
    return tree


def identify_candidate_removal_nodes(
    reduced_tree: nx.Graph,
    users: Iterable[Any],
) -> List[Any]:
    user_set = set(users)
    return sorted(v for v in reduced_tree.nodes() if v not in user_set)


def compute_tree_success_probability(
    tree: nx.Graph,
    swap_nodes: Iterable[Any],
    fusion_nodes: Iterable[Any],
    removal_nodes: Iterable[Any],
    p_op: float = 0.8,
    q_swap: ProbabilitySpec = 1.0,
    q_fus: ProbabilitySpec = 1.0,
    q_rem: ProbabilitySpec = 1.0,
    loss_coef_db_per_km: float = 0.2,
    weight_attr: str = "length_km",
) -> float:
    rho = 1.0

    for u, v, data in tree.edges(data=True):
        length_km = float(data.get(weight_attr, data.get("weight", 1.0)))
        rho *= edge_success_prob(length_km, p_op, loss_coef_db_per_km)

    for node in swap_nodes:
        rho *= probability_for(node, q_swap)

    for node in fusion_nodes:
        rho *= probability_for(node, q_fus)

    for node in removal_nodes:
        rho *= probability_for(node, q_rem)

    return float(rho)


def build_tree_operation_plan(
    tree: nx.Graph,
    users: Iterable[Any],
    p_op: float = 0.8,
    q_swap: ProbabilitySpec = 1.0,
    q_fus: ProbabilitySpec = 1.0,
    q_rem: ProbabilitySpec = 1.0,
    loss_coef_db_per_km: float = 0.2,
    weight_attr: str = "length_km",
    forced_fusion_nodes: Optional[Iterable[Any]] = None,
) -> TreeOperationPlan:
    users = list(users)
    forced_fusion_nodes = list(forced_fusion_nodes or [])
    retained_nodes = set(users).union(forced_fusion_nodes)
    reduced_tree, reduced_edge_paths = build_reduced_tree(tree, users, retained_nodes=retained_nodes)
    swap_nodes = sorted(
        v for v in tree.nodes()
        if v not in retained_nodes and tree.degree(v) == 2
    )
    fusion_nodes = sorted(set(identify_fusion_nodes(reduced_tree)).union(forced_fusion_nodes))
    candidate_removal_nodes = identify_candidate_removal_nodes(reduced_tree, users)
    rho = compute_tree_success_probability(
        tree=tree,
        swap_nodes=swap_nodes,
        fusion_nodes=fusion_nodes,
        removal_nodes=candidate_removal_nodes,
        p_op=p_op,
        q_swap=q_swap,
        q_fus=q_fus,
        q_rem=q_rem,
        loss_coef_db_per_km=loss_coef_db_per_km,
        weight_attr=weight_attr,
    )

    return TreeOperationPlan(
        tree=tree,
        users=users,
        swap_nodes=swap_nodes,
        fusion_nodes=fusion_nodes,
        candidate_removal_nodes=candidate_removal_nodes,
        reduced_tree=reduced_tree,
        reduced_edge_paths=reduced_edge_paths,
        rho=rho,
    )


def execute_tree_operation_plan(
    swapping,
    fusion,
    plan: TreeOperationPlan,
    current_time: int,
    q_swap: ProbabilitySpec = 1.0,
    q_fus: ProbabilitySpec = 1.0,
    q_swap_by_node: Optional[Dict[Any, float]] = None,
    q_fus_by_node: Optional[Dict[Any, float]] = None,
) -> bool:
    if isinstance(q_swap, dict) and q_swap_by_node is None:
        q_swap_by_node = q_swap
        q_swap = 1.0
    if isinstance(q_fus, dict) and q_fus_by_node is None:
        q_fus_by_node = q_fus
        q_fus = 1.0

    for path in plan.reduced_edge_paths.values():
        if len(path) > 2:
            success = swapping.entanglement_swapping(
                path=path,
                current_time=current_time,
                q_swap=probability_for(None, q_swap),
                q_swap_by_node=q_swap_by_node,
            )
            if not success:
                return False

    return fusion.fuse_tree(
        user_list=plan.users,
        reduced_tree=plan.reduced_tree,
        fusion_nodes=plan.fusion_nodes,
        current_time=current_time,
        q_fus=probability_for(None, q_fus),
        q_fus_by_node=q_fus_by_node,
        candidate_removal_nodes=plan.candidate_removal_nodes,
    )


def main() -> None:
    tree = nx.Graph()
    tree.add_edges_from([
        ("E", "X"),
        ("X", "A"),
        ("A", "C"),
        ("E", "D"),
        ("E", "Y"),
        ("Y", "Z"),
        ("Z", "B"),
    ])
    for u, v in tree.edges():
        tree[u][v]["length_km"] = 10

    plan = build_tree_operation_plan(
        tree=tree,
        users=["A", "B", "C", "D"],
        p_op=1.0,
        q_swap=0.9,
        q_fus=0.8,
        q_rem=1.0,
    )

    print("swap_nodes:", plan.swap_nodes)
    print("fusion_nodes:", plan.fusion_nodes)
    print("candidate_removal_nodes:", plan.candidate_removal_nodes)
    print("reduced_edges:", sorted(norm_edge(u, v) for u, v in plan.reduced_tree.edges()))
    print("reduced_edge_paths:", plan.reduced_edge_paths)
    print("rho:", round(plan.rho, 6))

    assert plan.swap_nodes == ["X", "Y", "Z"]
    assert plan.candidate_removal_nodes == ["E"]
    assert "E" in plan.fusion_nodes
    assert set(plan.reduced_tree.nodes()) == {"A", "B", "C", "D", "E"}

    star_tree = build_tree_from_paths({
        "A": ["E", "X", "A"],
        "B": ["E", "B"],
        "C": ["E", "C"],
    })
    star_plan = build_tree_operation_plan(
        tree=star_tree,
        users=["A", "B", "C"],
        forced_fusion_nodes=["E"],
    )
    assert star_plan.swap_nodes == ["X"]
    assert star_plan.fusion_nodes == ["E"]
    assert star_plan.candidate_removal_nodes == ["E"]


if __name__ == "__main__":
    main()
