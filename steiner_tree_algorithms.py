import networkx as nx
from itertools import combinations
import matplotlib.pyplot as plt
import random


def approximate_steiner_tree(graph: nx.Graph, terminals, weight_key='length_km'):
    """
    2-approx Steiner tree heuristic:
    1) Metric-closure on terminals (edge weight = shortest-path distance in `graph`)
    2) MST on the closure
    3) Expand each MST edge back to shortest paths in `graph`
    4) Run MST again on the expanded subgraph (with original weights) to remove cycles
    5) Prune non-terminal leaves
    Returns: an nx.Graph subgraph that spans all terminals.
    """

    terminals = list(terminals)
    if len(terminals) <= 1:
        # trivial
        return graph.subgraph(terminals).copy()

    # --- quick connectivity check among terminals ---
    # If any pair not connected in the original graph -> no Steiner tree exists.
    # (You can also check connected components once instead of pairwise.)
    for u, v in combinations(terminals, 2):
        if not nx.has_path(graph, u, v):
            raise nx.NetworkXNoPath(f"No path between terminals {u} and {v}.")

    # --- 1) Metric closure on terminals ---
    closure = nx.Graph()
    for u, v in combinations(terminals, 2):
        # shortest path length in the original graph
        dist = nx.shortest_path_length(graph, u, v, weight=weight_key)
        closure.add_edge(u, v, weight=dist)

    # --- 2) MST on metric closure ---
    closure_mst = nx.minimum_spanning_tree(closure, weight='weight')

    # --- 3) Expand MST edges back to shortest paths in original graph ---
    expanded = nx.Graph()
    for u, v in closure_mst.edges():
        path = nx.shortest_path(graph, source=u, target=v, weight=weight_key)
        for a, b in zip(path, path[1:]):
            # carry original weight
            w = graph[a][b].get(weight_key, 1.0)
            if expanded.has_edge(a, b):
                # keep the lighter edge if parallel choice arises (usually same)
                w = min(w, expanded[a][b].get(weight_key, w))
            expanded.add_edge(a, b, **{weight_key: w})

    # --- 4) De-cycle: MST on the expanded subgraph using original weights ---
    # If expanded has only one node or no edges, just return it
    if expanded.number_of_edges() > 0:
        expanded_mst = nx.minimum_spanning_tree(expanded, weight=weight_key)
    else:
        expanded_mst = expanded

    # --- 5) Prune non-terminal leaves ---
    steiner_tree = expanded_mst.copy()
    removed = True
    term_set = set(terminals)
    while removed:
        removed = False
        leaves = [n for n in steiner_tree.nodes()
                  if steiner_tree.degree(n) == 1 and n not in term_set]
        if leaves:
            steiner_tree.remove_nodes_from(leaves)
            removed = True

    return steiner_tree


def gen_multi_steiner_trees(G_orig, user_set, k_trees=5):
    """
    对边权做 ±5% 抖动生成多棵候选树；去重
    """
    trees = []

    for _ in range(k_trees):
        G = nx.Graph()
        for u, v, data in G_orig.edges(data=True):
            base = float(data.get("length_km", data.get("weight", 1.0)))
            jitter = 1.0 + 0.02 * random.random()
            # 抖动直接写回 length_km（算法实际使用的权重键）
            G.add_edge(
                u, v,
                length_km=base * jitter,
            )
        try:
            T = approximate_steiner_tree(G, user_set, weight_key='length_km')
            if T.number_of_edges() > 0:
                # 复制一份，避免后续外部修改
                H = nx.Graph()
                H.add_nodes_from(T.nodes(data=True))
                H.add_edges_from(T.edges(data=True))
                trees.append(H)
        except Exception:
            continue

    def _norm_edge(e):
        u, v = e[:2]
        return (u, v) if u < v else (v, u)

    # 去重（按无向规范化边集）
    uniq = {}
    for T in trees:
        key = frozenset(_norm_edge(e) for e in T.edges())
        uniq[key] = T
    return list(uniq.values())


def has_connecting_tree(subgraph, user_set):
    """
    Checks if a connecting tree exists among the users in the given subgraph.
    This is a proxy for whether a routing solution can be found.
    """
    if len(user_set) <= 1:
        return True

    if not all(node in subgraph.nodes for node in user_set):
        return False

    components = nx.connected_components(subgraph)
    user_set_set = set(user_set)
    return any(user_set_set.issubset(comp) for comp in components)


if __name__ == "__main__":
    from network_topology import Topology

    """
       0 —— 1 —— 2
       |    |    |
       3 —— 4 —— 5
       |    |    |
       6 —— 7 —— 8
    """
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
        (7, 8, 10)
    ]
    topo = Topology(edge_list)
    user_set = [0, 2, 7]

    trees = gen_multi_steiner_trees(topo.graph, user_set, k_trees=5)

    print(f"\n生成了 {len(trees)} 棵不同的候选 Steiner 树")
    for i, T in enumerate(trees, 1):
        total_len = sum(T[u][v]['length_km'] for u, v in T.edges())
        print(f"Tree {i}: 节点数={T.number_of_nodes()}, 边数={T.number_of_edges()}, 总长度={total_len:.2f}")
        print(T.edges)
