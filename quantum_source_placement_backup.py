import math
import random
import networkx as nx
from collections import Counter, defaultdict
from steiner_tree_algorithms import approximate_steiner_tree, gen_multi_steiner_trees
from seed_utils import derive_seed, set_global_seed
import single_slot_throughput_sweep_conditions as conditions

PAIR_COST = 1


class SourcePlacementBackup:
    """
    MT-OVERLAP: 两阶段启发式
      - 阶段A：给基线树(base tree)的每条边各放 1 对（弱边优先）
      - 阶段B：按与 base 的重叠度从高到低选择候选树，
               先在重叠边再在非重叠边上，用对数边际增益贪心加对
    成功率：p_e = p_op * (1 - (1 - 10^{-(loss*L)/10}))，q_e(x) = 1 - (1 - p_e)^x
    """

    def __init__(self, topo):
        self.topo = topo
        self.sources = []  # list[(u,v)]: 每个元素代表一对 source

    # ===== 外部接口 =====
    def place_sources_for_request(
        self,
        user_set,
        method="mt_overlap",
        cost_budget=None,
        max_per_edge=1,
        k_trees=5,
        p_op=1.0,
        loss_coef_dB_per_km=0.2,
        seed=None
    ):
        set_global_seed(seed)

        if method in ("OP", "steiner_tree"):
            return self._baseline_round_robin(user_set, base="steiner",
                                              cost_budget=cost_budget, max_per_edge=max_per_edge)
        if method in ("NOP", "all_edges"):
            return self._baseline_round_robin(user_set, base="all",
                                              cost_budget=cost_budget, max_per_edge=max_per_edge)

        if method.lower() != "mt_overlap":
            raise ValueError(f"Unknown method: {method}")

        # ==== MT-OVERLAP ====
        if cost_budget is None or cost_budget < 0:
            raise ValueError("[MT-OVERLAP] cost_budget 必须给定且非负")
        budget_pairs = int(cost_budget/PAIR_COST)

        # S0. 候选树
        # trees = gen_multi_steiner_trees(self.topo.graph, user_set, k_trees=k_trees)
        trees = self._generate_diverse_steiner_trees(user_set, K=k_trees,
                                                     lambda_overlap=0.8, weight_attr='length_km')

        if not trees:
            self.sources = []
            print("[MT-OVERLAP] 未生成候选树")
            return self.sources

        edge_sets = [set(self._norm_edge(e) for e in T.edges()) for T in trees]
        # E_union = sorted(set().union(*edge_sets))

        path_edges = self._userpair_k_shortest_paths(user_set, k_paths=2)
        E_union = sorted(set().union(*edge_sets).union(path_edges))

        if not E_union:
            self.sources = []
            print("[MT-OVERLAP] 候选树无边")
            return self.sources
        p_e = self._get_edge_success_prob(p_op=p_op, loss_coef_dB_per_km=loss_coef_dB_per_km)

        # S1. 选 base：用“每边 1 对”时的 Π_k^(1) 最大
        def q1(e):  # q_e(1)
            p = p_e.get(e, 0.0)
            return 1.0 - (1.0 - p) ** 1

        Pi1 = []
        for Ek in edge_sets:
            val = 1.0
            for e in Ek:
                val *= max(1e-15, q1(e))
            Pi1.append(val)
        base_idx = max(range(len(trees)), key=lambda i: Pi1[i])
        E_base = edge_sets[base_idx]
        print(f"Tree_base: {E_base}")

        # 统计重叠频次
        freq = Counter()
        for Ek in edge_sets:
            for e in Ek:
                freq[e] += 1
        print(f"freq: {freq}")

        # 对数边际增益（+1 对）
        def log_gain_one(edge, x_e):
            p = p_e.get(edge, 0.0)
            if p <= 0.0:
                return -1e18
            base = (1.0 - p) ** x_e
            nxt = (1.0 - p) ** (x_e + 1)
            if base >= 1.0:
                return -1e18
            return math.log(1.0 - nxt) - math.log(1.0 - base)

        # 可行性检查
        def feasible_plus1(e, x):
            if x[e] >= max_per_edge:
                return False
            return True

        # 初始化
        x = {e: 0 for e in E_union}
        used = 0

        # S2. 阶段A：给 base tree 每边 1 对（弱边先补）
        base_edges_sorted = sorted(E_base, key=lambda e: p_e.get(e, 0.0))  # p 小先补
        for e in base_edges_sorted:
            if used >= budget_pairs:
                break
            if feasible_plus1(e, x):
                x[e] += 1
                used += 1

        # S3. 阶段B：按与 base 的重叠度从高到低择树，再部署
        # overlap_k = |E_k ∩ E_base| / |E_k|
        overlap_order = sorted(
            [i for i in range(len(trees)) if i != base_idx],
            key=lambda i: (len(edge_sets[i] & E_base) / max(1, len(edge_sets[i]))),
            reverse=True
        )

        def greedy_fill_on_edges(edge_pool):
            nonlocal used, x
            if used >= budget_pairs:
                return
            # 两层候选：先重叠边，再非重叠边；同层内按 log 增益降序
            edges_in_base = [e for e in edge_pool if e in E_base]
            edges_not_inbase = [e for e in edge_pool if e not in E_base]

            while used < budget_pairs:
                # 每一小步都重算最优边（更稳）
                cand_in = sorted(
                    (e for e in edges_in_base if feasible_plus1(e, x)),
                    key=lambda e: (log_gain_one(e, x[e]), freq[e]), reverse=True
                )
                if cand_in:
                    best = cand_in[0]
                else:
                    cand_out = sorted(
                        (e for e in edges_not_inbase if feasible_plus1(e, x)),
                        key=lambda e: (log_gain_one(e, x[e]), freq[e]), reverse=True
                    )
                    if not cand_out:
                        break
                    best = cand_out[0]

                x[best] += 1
                used += 1

        for i in overlap_order:
            if used >= budget_pairs:
                break
            greedy_fill_on_edges(list(edge_sets[i]))

        # 扫尾：若还有预算，在并集边上全局优先级 (in_base, freq, gain)
        while used < budget_pairs:
            candidates = [e for e in E_union if feasible_plus1(e, x)]
            if not candidates:
                break
            best = max(
                candidates,
                key=lambda e: (1 if e in E_base else 0, freq[e], log_gain_one(e, x[e]))
            )
            x[best] += 1
            used += 1

        # 展开 sources
        self.sources = []
        for e, cnt in x.items():
            self.sources.extend([e] * cnt)

        print(f"[SRP-MT-OVERLAP] base_idx={base_idx}, placed_pairs={used}, "
              f"budget_pairs={budget_pairs}, union_edges={len(E_union)}")
        print(f"[SourcePlacement] Total cost: {self.compute_cost()} (PAIR_COST={PAIR_COST})")
        return self.sources

    def _userpair_k_shortest_paths(self, user_set, k_paths=2, weight_attr='length_km'):
        """
        For each unordered user pair, collect up to k shortest simple paths (by length_attr),
        and return the union set of edges on those paths.

        Returns:
            edge_set: set of undirected edge keys (u,v)
        """
        G = self.topo.graph
        edge_set = set()
        # build edge weight getter
        def length_of(u, v):
            data = G.get_edge_data(u, v, {})
            if weight_attr in data:
                return float(data[weight_attr])
            return float(data.get('length_km', data.get('weight', 1.0)))

        users = list(user_set)
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                s, t = users[i], users[j]
                # k shortest simple paths by length
                try:
                    gen = nx.shortest_simple_paths(G, s, t, weight=weight_attr)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                c = 0
                for path in gen:
                    # add path edges
                    for a, b in zip(path, path[1:]):
                        edge_set.add(self._norm_edge((a, b)))
                    c += 1
                    if c >= k_paths:
                        break
        return edge_set

    # ===== baseline（OP / NOP） =====
    def _baseline_round_robin(self, user_set, base="steiner", cost_budget=None, max_per_edge=1):
        if base == "steiner":
            subgraph = approximate_steiner_tree(self.topo.graph, user_set)
            base_edges = list(subgraph.edges())
        else:
            base_edges = list(self.topo.get_edges())

        base_keys = sorted({self._norm_edge(e) for e in base_edges})
        if not base_keys:
            self.sources = []
            print("[SourcePlacement] No candidate edges found.")
            return self.sources

        self.sources = []
        per_edge_count = {k: 0 for k in base_keys}

        if cost_budget is None:
            for (u, v) in base_keys:
                if per_edge_count[(u, v)] < max_per_edge:
                    self.sources.append((u, v))
                    per_edge_count[(u, v)] += 1
            print(f"[SourcePlacement] Method: {base}, Sources placed: {self.sources}")
            print(f"[SourcePlacement] Total cost: {self.compute_cost()}")
            print("[SourcePlacement] Cost budget: None")
            return self.sources

        if cost_budget < 0:
            raise ValueError("cost_budget must be non-negative")
        budget_pairs = int(cost_budget)

        capacity_pairs = len(base_keys) * max_per_edge
        target_pairs = min(budget_pairs, capacity_pairs)

        placed = 0
        idx = 0
        n = len(base_keys)
        while placed < target_pairs:
            u, v = base_keys[idx % n]
            if per_edge_count[(u, v)] < max_per_edge:
                self.sources.append((u, v))
                per_edge_count[(u, v)] += 1
                placed += 1
            idx += 1
            if idx >= n and all(per_edge_count[k] >= max_per_edge for k in base_keys):
                break

        print(f"[SourcePlacement] Method: {base}, Sources placed: {self.sources}")
        print(f"[SourcePlacement] Total cost: {self.compute_cost()} (target_pairs={target_pairs})")
        print(f"[SourcePlacement] Cost budget (pairs): {cost_budget}, max_per_edge={max_per_edge}, "
              f"capacity_pairs={capacity_pairs}, used_pairs={placed}")
        return self.sources

    def compute_cost(self):
        return len(self.sources) * PAIR_COST

    @staticmethod
    def _norm_edge(e):
        u, v = e[:2]
        return (u, v) if u < v else (v, u)

    def _get_edge_success_prob(self, p_op, loss_coef_dB_per_km):
        """
        p_loss = 1 - 10^{-(loss_coef * L)/10}
        p_e    = p_op * (1 - p_loss)
        """
        pe = {}
        G = self.topo.graph
        for (u, v, data) in G.edges(data=True):
            key = self._norm_edge((u, v))
            # L = float(data.get("length_km", data.get("weight", 1.0)))
            L = float(data.get("length_km"))
            p_loss = 1.0 - (10.0 ** (-(loss_coef_dB_per_km * L) / 10.0))
            p = p_op * (1.0 - p_loss)
            pe[key] = max(0.0, min(1.0, p))
        return pe

    def _generate_diverse_steiner_trees(self, user_set, K=5, lambda_overlap=0.8, weight_attr='length_km'):
        """
        Generate K 'diverse' Steiner trees by inflating the weight of edges
        that were already used in previous trees (overlap penalty).
        Also add a tiny random jitter to diversify.

        Returns:
            trees: list of sets of undirected edge keys (u,v) per Steiner tree
        """

        def _normalize_edge_tuple(e):
            """Return undirected edge key as a sorted 2-tuple (u, v)."""
            u, v = e[:2]
            return (u, v) if u < v else (v, u)

        G = self.topo.graph.copy()
        # ensure each edge has a base weight
        for u, v in G.edges():
            if weight_attr not in G[u][v]:
                # fallback on existing length/weight, else unit
                L = G[u][v].get('length_km', G[u][v].get('weight', 1.0))
                G[u][v][weight_attr] = float(L)

        used_count = defaultdict(int)
        trees = []
        for k in range(K):
            # build modified weights with overlap penalty + tiny jitter
            for u, v in G.edges():
                base_w = float(G[u][v][weight_attr])
                pen = 1.0 + lambda_overlap * used_count[_normalize_edge_tuple((u, v))]
                jitter = 1.0 + 0.02 * random.random()  # small randomness
                G[u][v]['_tmp_w'] = base_w * pen * jitter
            try:
                T = approximate_steiner_tree(G, user_set)  # NOTE: your function should accept weight param if needed
                # If your approximate_steiner_tree accepts weight kw, pass weight='_tmp_w'.
                # e.g., T = approximate_steiner_tree(G, user_set, weight='_tmp_w')

                if T.number_of_edges() > 0:
                    # 复制一份，避免后续外部修改
                    H = nx.Graph()
                    H.add_nodes_from(T.nodes(data=True))
                    H.add_edges_from(T.edges(data=True))
                    trees.append(H)

                    # clean temp weights if needed
                    for u, v in G.edges():
                        if '_tmp_w' in G[u][v]:
                            del G[u][v]['_tmp_w']

            except Exception:
                continue

        # 去重（按无向规范化边集）
        uniq = {}
        for T in trees:
            key = frozenset(_normalize_edge_tuple(e) for e in T.edges())
            uniq[key] = T

        return list(uniq.values())


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
    users = [0, 2, 7]

    sp = SourcePlacementBackup(topo)
    placed = sp.place_sources_for_request(
        users,
        method="mt_overlap",
        cost_budget=20,
        max_per_edge=3,
        k_trees=3,
        p_op=0.9,
        loss_coef_dB_per_km=0.2,
        seed=derive_seed(conditions.RANDOM_SEED, "quantum-source-placement-backup", "demo")
    )
    print("Placed pairs:", placed)


# import math
# import random
# import networkx as nx
# from collections import Counter
# from steiner_tree_algorithms import approximate_steiner_tree
#
# # ===== 全局：每对 source 的成本（你项目里统一改这里） =====
# PAIR_COST = 1
#
#
# class SourcePlacementBackup:
#     """
#     SRP-MT-OVERLAP: 两阶段启发式
#       - 阶段A：给基线树(base tree)的每条边各放 1 对（弱边优先）
#       - 阶段B：按与 base 的重叠度从高到低选择候选树，
#                先在重叠边再在非重叠边上，用对数边际增益贪心加对
#     约束：全局预算、每边上限、（可选）节点内存
#     成功率：p_e = p_op * (1 - (1 - 10^{-(loss*L)/10}))，q_e(x) = 1 - (1 - p_e)^x
#     """
#
#     def __init__(self, topo):
#         self.topo = topo
#         self.sources = []  # list[(u,v)]: 每个元素代表一对 source
#
#     # ===== 外部接口 =====
#     def place_sources_for_request(
#         self,
#         user_set,
#         method="mt_overlap",   # 仅保留该方法；也支持 OP/NOP 作为基线
#         cost_budget=None,          # 预算（按“对”计）
#         max_per_edge=1,
#         k_trees=5,
#         p_op=1.0,
#         loss_coef_dB_per_km=0.2,
#         seed=1,
#         node_memory=None           # 可选: {node: cap}
#     ):
#         random.seed(seed)
#
#         # 基线方法兼容（可选：保留以便对比）
#         if method in ("OP", "steiner_tree"):
#             return self._baseline_round_robin(user_set, base="steiner",
#                                               cost_budget=cost_budget, max_per_edge=max_per_edge)
#         if method in ("NOP", "all_edges"):
#             return self._baseline_round_robin(user_set, base="all",
#                                               cost_budget=cost_budget, max_per_edge=max_per_edge)
#
#         if method.lower() != "mt_overlap":
#             raise ValueError(f"Unknown method: {method}")
#
#         # ==== SRP-MT-OVERLAP 主流程 ====
#         if cost_budget is None or cost_budget < 0:
#             raise ValueError("[SRP-MT-OVERLAP] cost_budget 必须给定且非负")
#         budget_pairs = int(cost_budget)
#
#         # S0. 候选树
#         trees = self._gen_multi_steiner_trees(user_set, k_trees=k_trees, seed=seed)
#         if not trees:
#             self.sources = []
#             print("[SRP-MT-OVERLAP] 未生成候选树。")
#             return self.sources
#
#         edge_sets = [set(self._norm_edge(e) for e in T.edges()) for T in trees]
#         E_union = sorted(set().union(*edge_sets))
#         if not E_union:
#             self.sources = []
#             print("[SRP-MT-OVERLAP] 候选树无边。")
#             return self.sources
#
#         # p_e by physical model
#         p_e = self._get_edge_success_prob(p_op=p_op, loss_coef_dB_per_km=loss_coef_dB_per_km)
#
#         # S1. 选 base：用“每边 1 对”时的 Π_k^(1) 最大
#         def q1(e):  # q_e(1)
#             p = p_e.get(e, 0.0)
#             return 1.0 - (1.0 - p) ** 1
#         Pi1 = []
#         for Ek in edge_sets:
#             val = 1.0
#             for e in Ek:
#                 val *= max(1e-15, q1(e))
#             Pi1.append(val)
#         base_idx = max(range(len(trees)), key=lambda i: Pi1[i])
#         E_base = edge_sets[base_idx]
#
#         # 统计重叠频次
#         freq = Counter()
#         for Ek in edge_sets:
#             for e in Ek:
#                 freq[e] += 1
#
#         # 对数边际增益（+1 对）
#         def log_gain_one(edge, x_e):
#             p = p_e.get(edge, 0.0)
#             if p <= 0.0:
#                 return -1e18
#             base = (1.0 - p) ** x_e
#             nxt = (1.0 - p) ** (x_e + 1)
#             if base >= 1.0:
#                 return -1e18
#             return math.log(1.0 - nxt) - math.log(1.0 - base)
#
#         # 可行性检查
#         def feasible_plus1(e, x, mem_left):
#             if x[e] >= max_per_edge:
#                 return False
#             if mem_left is None:
#                 return True
#             u, v = e
#             return (mem_left.get(u, 1 << 30) >= 1) and (mem_left.get(v, 1 << 30) >= 1)
#
#         # 初始化
#         x = {e: 0 for e in E_union}
#         used = 0
#         mem_left = dict(node_memory) if node_memory is not None else None
#
#         # S2. 阶段A：给 base tree 每边 1 对（弱边先补）
#         base_edges_sorted = sorted(E_base, key=lambda e: p_e.get(e, 0.0))  # p 小先补
#         for e in base_edges_sorted:
#             if used >= budget_pairs:
#                 break
#             if feasible_plus1(e, x, mem_left):
#                 x[e] += 1
#                 used += 1
#                 if mem_left is not None:
#                     u, v = e
#                     mem_left[u] -= 1
#                     mem_left[v] -= 1
#
#         # S3. 阶段B：按与 base 的重叠度从高到低择树，再部署
#         # overlap_k = |E_k ∩ E_base| / |E_k|
#         overlap_order = sorted(
#             [i for i in range(len(trees)) if i != base_idx],
#             key=lambda i: (len(edge_sets[i] & E_base) / max(1, len(edge_sets[i]))),
#             reverse=True
#         )
#
#         def greedy_fill_on_edges(edge_pool):
#             nonlocal used, x, mem_left
#             if used >= budget_pairs:
#                 return
#             # 两层候选：先重叠边，再非重叠边；同层内按 log 增益降序
#             edges_in_base = [e for e in edge_pool if e in E_base]
#             edges_not_inbase = [e for e in edge_pool if e not in E_base]
#
#             while used < budget_pairs:
#                 # 每一小步都重算最优边（更稳）
#                 cand_in = sorted(
#                     (e for e in edges_in_base if feasible_plus1(e, x, mem_left)),
#                     key=lambda e: (log_gain_one(e, x[e]), freq[e]), reverse=True
#                 )
#                 if cand_in:
#                     best = cand_in[0]
#                 else:
#                     cand_out = sorted(
#                         (e for e in edges_not_inbase if feasible_plus1(e, x, mem_left)),
#                         key=lambda e: (log_gain_one(e, x[e]), freq[e]), reverse=True
#                     )
#                     if not cand_out:
#                         break
#                     best = cand_out[0]
#
#                 x[best] += 1
#                 used += 1
#                 if mem_left is not None:
#                     u, v = best
#                     mem_left[u] -= 1
#                     mem_left[v] -= 1
#
#         for i in overlap_order:
#             if used >= budget_pairs:
#                 break
#             greedy_fill_on_edges(list(edge_sets[i]))
#
#         # 扫尾：若还有预算，在并集边上全局优先级 (in_base, freq, gain)
#         while used < budget_pairs:
#             candidates = [e for e in E_union if feasible_plus1(e, x, mem_left)]
#             if not candidates:
#                 break
#             best = max(
#                 candidates,
#                 key=lambda e: (1 if e in E_base else 0, freq[e], log_gain_one(e, x[e]))
#             )
#             x[best] += 1
#             used += 1
#             if mem_left is not None:
#                 u, v = best
#                 mem_left[u] -= 1
#                 mem_left[v] -= 1
#
#         # 展开 sources
#         self.sources = []
#         for e, cnt in x.items():
#             self.sources.extend([e] * cnt)
#
#         print(f"[SRP-MT-OVERLAP] base_idx={base_idx}, placed_pairs={used}, "
#               f"budget_pairs={budget_pairs}, union_edges={len(E_union)}")
#         print(f"[SourcePlacement] Total cost: {self.compute_cost()} (PAIR_COST={PAIR_COST})")
#         return self.sources
#
#     # ===== 基线（OP / NOP）保留以便对比 =====
#     def _baseline_round_robin(self, user_set, base="steiner", cost_budget=None, max_per_edge=1):
#         if base == "steiner":
#             subgraph = approximate_steiner_tree(self.topo.graph, user_set)
#             base_edges = list(subgraph.edges())
#         else:
#             base_edges = list(self.topo.get_edges())
#
#         base_keys = sorted({self._norm_edge(e) for e in base_edges})
#         if not base_keys:
#             self.sources = []
#             print("[SourcePlacement] No candidate edges found.")
#             return self.sources
#
#         self.sources = []
#         per_edge_count = {k: 0 for k in base_keys}
#
#         if cost_budget is None:
#             for (u, v) in base_keys:
#                 if per_edge_count[(u, v)] < max_per_edge:
#                     self.sources.append((u, v))
#                     per_edge_count[(u, v)] += 1
#             print(f"[SourcePlacement] Method: {base}, Sources placed: {self.sources}")
#             print(f"[SourcePlacement] Total cost: {self.compute_cost()}")
#             print("[SourcePlacement] Cost budget: None")
#             return self.sources
#
#         if cost_budget < 0:
#             raise ValueError("cost_budget must be non-negative")
#         budget_pairs = int(cost_budget)
#
#         capacity_pairs = len(base_keys) * max_per_edge
#         target_pairs = min(budget_pairs, capacity_pairs)
#
#         placed = 0
#         idx = 0
#         n = len(base_keys)
#         while placed < target_pairs:
#             u, v = base_keys[idx % n]
#             if per_edge_count[(u, v)] < max_per_edge:
#                 self.sources.append((u, v))
#                 per_edge_count[(u, v)] += 1
#                 placed += 1
#             idx += 1
#             if idx >= n and all(per_edge_count[k] >= max_per_edge for k in base_keys):
#                 break
#
#         print(f"[SourcePlacement] Method: {base}, Sources placed: {self.sources}")
#         print(f"[SourcePlacement] Total cost: {self.compute_cost()} (target_pairs={target_pairs})")
#         print(f"[SourcePlacement] Cost budget (pairs): {cost_budget}, max_per_edge={max_per_edge}, "
#               f"capacity_pairs={capacity_pairs}, used_pairs={placed}")
#         return self.sources
#
#     # ===== 工具 =====
#     def compute_cost(self):
#         return len(self.sources) * PAIR_COST
#
#     @staticmethod
#     def _norm_edge(e):
#         u, v = e[:2]
#         return (u, v) if u < v else (v, u)
#
#     def _get_edge_success_prob(self, p_op, loss_coef_dB_per_km):
#         """
#         p_loss = 1 - 10^{-(loss_coef * L)/10}
#         p_e    = p_op * (1 - p_loss)
#         L 优先取 'length_km'；无则取 'weight'；再无则 1.0
#         """
#         pe = {}
#         G = self.topo.graph
#         for (u, v, data) in G.edges(data=True):
#             key = self._norm_edge((u, v))
#             L = float(data.get("length_km", data.get("weight", 1.0)))
#             p_loss = 1.0 - (10.0 ** (-(loss_coef_dB_per_km * L) / 10.0))
#             p = p_op * (1.0 - p_loss)
#             pe[key] = max(0.0, min(1.0, p))
#         return pe
#
#     def _gen_multi_steiner_trees(self, user_set, k_trees=5, seed=1):
#         """
#         对边权做 ±5% 抖动生成多棵候选树；去重
#         """
#         rng = random.Random(seed)
#         trees = []
#         G_orig = self.topo.graph
#
#         for _ in range(k_trees):
#             G = nx.Graph()
#             for u, v, data in G_orig.edges(data=True):
#                 w = float(data.get("weight", 1.0))
#                 jitter = 1.0 + rng.uniform(-0.05, 0.05)
#                 # 保持 length_km 存在（若原无 length_km，用 weight 做近似）
#                 G.add_edge(u, v, weight=w * jitter,
#                            length_km=data.get("length_km", w))
#             try:
#                 T = approximate_steiner_tree(G, user_set)
#                 if T.number_of_edges() > 0:
#                     H = nx.Graph()
#                     H.add_nodes_from(T.nodes())
#                     H.add_edges_from(T.edges(data=True))
#                     trees.append(H)
#             except Exception:
#                 continue
#
#         # 去重（按无向规范化边集）
#         uniq = {}
#         for T in trees:
#             key = tuple(sorted({self._norm_edge(e) for e in T.edges()}))
#             uniq[key] = T
#         return list(uniq.values())
#
#
# # ===== 示例 =====
# if __name__ == "__main__":
#     from network_topology import Topology
#
#     """
#        0 —— 1 —— 2
#        |    |    |
#        3 —— 4 —— 5
#        |    |    |
#        6 —— 7 —— 8
#     """
#     edge_list = [
#         (0, 1, 10),
#         (0, 3, 10),
#         (1, 2, 10),
#         (1, 4, 10),
#         (2, 5, 10),
#         (3, 4, 10),
#         (3, 6, 10),
#         (4, 7, 10),
#         (5, 8, 10),
#         (6, 7, 10),
#         (7, 8, 10)
#     ]
#     topo = Topology(edge_list)
#     users = [0, 2, 7]
#
#     sp = SourcePlacement(topo)
#     placed = sp.place_sources_for_request(
#         users,
#         method="srp_mt_overlap",
#         cost_budget=10,         # 10 对；总 cost = 10 * PAIR_COST
#         max_per_edge=3,
#         k_trees=6,
#         p_op=0.9,
#         loss_coef_dB_per_km=0.2,
#         seed=42,
#         node_memory=None
#     )
#     print("Placed pairs:", placed)
#     print("Total cost:", sp.compute_cost(), "(PAIR_COST =", PAIR_COST, ")")
