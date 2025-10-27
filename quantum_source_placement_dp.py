"""
Source placement via:
1) Candidate Generation (K-Steiner trees + user-pair k-shortest paths)
2) Composite Scoring function Score(e)
3) Multiple-Choice (Grouped) DP to select how many pairs per edge under a budget
"""

import random
import networkx as nx
from collections import defaultdict
from steiner_tree_algorithms import approximate_steiner_tree, gen_multi_steiner_trees

PAIR_COST = 1


def _normalize_edge_tuple(e):
    """Return undirected edge key as a sorted 2-tuple (u, v)."""
    u, v = e[:2]
    return (u, v) if u < v else (v, u)


def _minmax_normalize(d, eps=1e-12):
    """
    Min-max normalize a dict of edge->value into [0,1].
    If all values equal, return zeros (no discrimination).
    """
    if not d:
        return {}
    vals = list(d.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < eps:
        return {k: 0.0 for k in d}
    return {k: (d[k] - lo) / (hi - lo + eps) for k in d}


class SourcePlacementDP:
    """
    Source placement with:
      1) Candidate edges from diverse K-Steiner trees + user-pair k-shortest paths
      2) Composite Score(e)
      3) Grouped DP (Multiple-Choice Knapsack) to allocate multiple pairs per edge
    """

    def __init__(self, topo):
        self.topo = topo
        self.sources = []  # list of edges (u,v) repeated per pair

    # 1) Candidate Generation
    # -----------------------------
    def _edge_length(self, e, default_len=1.0):
        """
        Extract length for edge e from topo.graph. If not present, use default_len.
        """
        u, v = e
        data = self.topo.graph.get_edge_data(u, v, default={})
        if 'length_km' in data:
            return float(data['length_km'])
        return float(default_len)

    def _generate_diverse_steiner_trees(self, user_set, K=5, lambda_overlap=0.8, weight_attr='length_km'):
        """
        Generate K 'diverse' Steiner trees by inflating the weight of edges
        that were already used in previous trees (overlap penalty).
        Also add a tiny random jitter to diversify.

        Returns:
            trees: list of sets of undirected edge keys (u,v) per Steiner tree
        """
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

            T = approximate_steiner_tree(G, user_set)  # NOTE: your function should accept weight param if needed
            # If your approximate_steiner_tree accepts weight kw, pass weight='_tmp_w'.
            # e.g., T = approximate_steiner_tree(G, user_set, weight='_tmp_w')

            # Collect undirected edges from T
            tree_edges = set()
            for (u, v) in T.edges():
                tree_edges.add(_normalize_edge_tuple((u, v)))
                used_count[_normalize_edge_tuple((u, v))] += 1

            trees.append(tree_edges)

            # clean temp weights if needed
            for u, v in G.edges():
                if '_tmp_w' in G[u][v]:
                    del G[u][v]['_tmp_w']

        return trees

    def _userpair_k_shortest_paths(self, user_set, k_paths=2, weight_attr='length_km'):
        """
        For each unordered user pair, collect up to k shortest simple paths (by length_attr),
        and return the union set of edges on those paths.

        Returns:
            edge_set: set of undirected edge keys (u,v)
        """
        G = self.topo.graph
        edge_set = set()

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
                        edge_set.add(_normalize_edge_tuple((a, b)))
                    c += 1
                    if c >= k_paths:
                        break
        return edge_set

    def build_candidate_edges(self, user_set, K_steiner=5, k_paths=2,
                              weight_attr='length_km', union_strategy='union'):
        """
        Create candidate edge set as union (or intersection) of:
          - K diverse Steiner trees
          - user-pair k shortest paths

        Returns:
            cand_edges: sorted list of undirected edge tuples
            provenance: dict edge -> {'in_steiner': cnt, 'in_paths': cnt}
        """
        trees = self._generate_diverse_steiner_trees(user_set, K=K_steiner,
                                                     lambda_overlap=0.8, weight_attr=weight_attr)
        steiner_union = set().union(*trees) if trees else set()

        path_edges = self._userpair_k_shortest_paths(user_set, k_paths=k_paths,
                                                     weight_attr=weight_attr)

        if union_strategy == 'intersection':
            # rare: only edges that appear in both sets
            cand = steiner_union.intersection(path_edges)
        else:
            # default: union
            cand = steiner_union.union(path_edges)

        # provenance counts for F_demand and overlap metrics
        prov = {e: {'in_steiner': 0, 'in_paths': 0} for e in cand}
        for e in cand:
            # count appearances in trees
            prov[e]['in_steiner'] = sum(1 for T in trees if e in T)
            # whether it appears in any user-pair path set
            prov[e]['in_paths'] = 1 if e in path_edges else 0

        return sorted(cand), prov

    # -----------------------------
    # 2) Composite Score
    # -----------------------------
    def _edge_success_prob(self, e, p_map=None, p_op=0.9, loss_coef_dB_per_km=0.2):
        # normalize to undirected key
        u, v = e[:2]
        key = (u, v) if u < v else (v, u)

        # 1) prefer provided p_map
        if p_map and key in p_map:
            return float(p_map[key])

        L = self._edge_length(key, default_len=1.0)  # expects km if your graph stores km
        p_loss = 1.0 - (10.0 ** (-(loss_coef_dB_per_km * L) / 10.0))
        p_e = p_op * (1.0 - p_loss)
        return float(p_e)

    def _edge_betweenness_users(self, users):
        """
        User-pair-focused 'betweenness-like' measure:
          for each user pair, count number of shortest paths and how often each edge
          lies on them (fractional if multiple equal-length shortest paths).
        Returns:
          betw: dict edge->score (NOT normalized)
        """
        G = self.topo.graph
        betw = defaultdict(float)

        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                s, t = users[i], users[j]
                # all shortest paths by length attribute (fall back to unweighted)
                try:
                    paths = list(nx.all_shortest_paths(G, s, t, weight='length_km'))
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if not paths:
                    continue
                denom = float(len(paths))
                for path in paths:
                    edges = [_normalize_edge_tuple((a,b)) for a,b in zip(path, path[1:]) ]
                    for e in edges:
                        betw[e] += 1.0/denom

        return betw

    def score_edges(self, cand_edges, users, provenance,
                    p_map=None, p_op=0.9,
                    w_topo=0.25, w_demand=0.35, w_quality=0.35, w_overlap=0.0):
        """
        Compute composite Score(e) for each candidate edge.

        Components:
          F_topo(e): user-focused betweenness (normalized)
          F_demand(e): frequency in K Steiner trees / path-set (normalized)
          F_qual(e): physical quality via success probability (normalized)
          F_overlap(e): optional overlap penalty (normalized) -- default off

        Returns:
          score: dict edge->float
          parts: dict of parts for diagnostics
        """
        # 2.1 Topological importance (user-focused betweenness)
        F_topo_raw = self._edge_betweenness_users(users)
        F_topo = _normalize_edge_tuple_dict = _minmax_normalize(F_topo_raw)

        # 2.2 Demand relevance (appearance frequency)
        demand_raw = {}
        for e in cand_edges:
            # example: freq in Steiner trees (0..K), plus whether in paths
            s_cnt = provenance[e].get('in_steiner', 0)
            p_cnt = provenance[e].get('in_paths', 0)  # 0/1
            demand_raw[e] = float(s_cnt + p_cnt)
        F_demand = _minmax_normalize(demand_raw)

        # 2.3 Physical quality (success probability)
        qual_raw = {e: self._edge_success_prob(e, p_map=p_map, p_op=p_op) for e in cand_edges}
        F_qual = _minmax_normalize(qual_raw)

        # 2.4 Overlap penalty (optional): here use "congestion" proxy = Steiner freq
        #    The more an edge appears across trees, the higher the penalty.
        #    You can refine this with path-overlap structure if needed.
        if w_overlap > 0.0:
            overlap_raw = {e: float(provenance[e].get('in_steiner', 0)) for e in cand_edges}
            F_overlap = _minmax_normalize(overlap_raw)
        else:
            F_overlap = {e: 0.0 for e in cand_edges}

        # 2.5 Combine
        score = {}
        for e in cand_edges:
            score[e] = (w_topo * F_topo.get(e, 0.0)
                        + w_demand * F_demand.get(e, 0.0)
                        + w_quality * F_qual.get(e, 0.0)
                        - w_overlap * F_overlap.get(e, 0.0))

        parts = {
            'F_topo': F_topo,
            'F_demand': F_demand,
            'F_qual': F_qual,
            'F_overlap': F_overlap
        }
        return score, parts

    # -----------------------------
    # 3) Grouped DP Optimization
    # -----------------------------
    def _value_function_prob(self, base_score, p_e, y):
        """
        Value of placing y pairs on edge with base_score and success-prob p_e:
          V_e(y) = base_score * (1 - (1 - p_e)^y)
        This encodes diminishing returns.
        """
        if y <= 0:
            return 0.0
        return base_score * (1.0 - (1.0 - p_e) ** y)

    def optimize_grouped_dp(self, cand_edges, score, p_map=None, p_op=0.9,
                            cost_budget=12, pair_cost=1, max_per_edge=3,
                            value_model='prob'):
        """
        Multiple-Choice (Grouped) DP:
          For each edge e in cand_edges (a 'group'), choose y in {0..max_per_edge}
          pairs under total budget (in pairs) = cost_budget // pair_cost.

        Returns:
            per_edge_count: dict edge->y selected
            total_pairs: sum(y)
            dp_value: objective value
        """
        B = int(cost_budget // pair_cost)
        n = len(cand_edges)

        # allow per-edge cap dict
        if isinstance(max_per_edge, int):
            cap_map = {e: int(max_per_edge) for e in cand_edges}
        else:
            cap_map = {e: int(max_per_edge.get(e, 0)) for e in cand_edges}

        # precompute probabilities if needed
        p_e_map = {e: self._edge_success_prob(e, p_map=p_map, p_op=p_op) for e in cand_edges}

        # DP tables
        dp = [[0.0]*(B+1) for _ in range(n+1)]
        choice = [[0]*(B+1) for _ in range(n+1)]  # how many y chosen for edge i at budget b

        for i, e in enumerate(cand_edges, start=1):
            s_e = float(score.get(e, 0.0))
            p_e = float(p_e_map.get(e, 0.0))
            cap = cap_map[e]

            # precompute V_e(y)
            V = [0.0]*(cap+1)
            for y in range(1, cap+1):
                if value_model == 'prob':
                    V[y] = self._value_function_prob(s_e, p_e, y)
                else:
                    V[y] = s_e * y  # linear model (no diminishing returns)

            for b in range(B+1):
                best_val, best_y = dp[i-1][b], 0
                # try y pairs on this edge
                for y in range(1, cap+1):
                    cost_y = y * pair_cost
                    if cost_y <= (b * pair_cost):  # both in 'pairs' units
                        # previous state uses (b - y) pairs
                        val = dp[i-1][b - y] + V[y]
                        if val > best_val:
                            best_val, best_y = val, y
                    else:
                        break
                dp[i][b] = best_val
                choice[i][b] = best_y

        # backtrack to get selections
        per_edge_count = {e: 0 for e in cand_edges}
        b = B
        for i in range(n, 0, -1):
            y = choice[i][b]
            if y > 0:
                e = cand_edges[i-1]
                per_edge_count[e] = y
                b -= y

        total_pairs = sum(per_edge_count.values())
        return per_edge_count, total_pairs, dp[n][B]

    # -----------------------------
    # High-level API
    # -----------------------------
    def place_sources_for_request(self, user_set,
                                  cost_budget=12, max_per_edge=3,
                                  K_steiner=5, k_paths=2, weight_attr='length_km',
                                  w_topo=0.25, w_demand=0.35, w_quality=0.35, w_overlap=0.0,
                                  p_map=None, p_op=0.9, value_model='prob'):
        """
        Full pipeline:
          1) Candidate edges (diverse K-Steiner + user-pair k-shortest)
          2) Score each candidate edge
          3) Grouped-DP select how many pairs per edge

        Returns:
          sources: list of edges [(u,v), ...] repeated per pair
          debug: dict with candidates, scores, parts, allocation, etc.
        """
        # Step 1: candidates
        cand_edges, provenance = self.build_candidate_edges(
            user_set, K_steiner=K_steiner, k_paths=k_paths, weight_attr=weight_attr
        )

        if not cand_edges:
            self.sources = []
            return self.sources, {
                'candidates': [],
                'scores': {},
                'parts': {},
                'allocation': {},
                'total_pairs': 0,
                'dp_value': 0.0
            }

        # Step 2: composite scores
        scores, parts = self.score_edges(
            cand_edges, users=list(user_set), provenance=provenance, p_map=p_map, p_op=p_op,
            w_topo=w_topo, w_demand=w_demand, w_quality=w_quality, w_overlap=w_overlap
        )

        # Step 3: grouped DP optimization
        alloc, total_pairs, dp_value = self.optimize_grouped_dp(
            cand_edges, score=scores, p_map=p_map, p_op=p_op,
            cost_budget=cost_budget, pair_cost=PAIR_COST,
            max_per_edge=max_per_edge, value_model=value_model
        )

        # expand to list of pairs to be consistent with your simulator interface
        self.sources = []
        for e, y in alloc.items():
            for _ in range(y):
                self.sources.append(e)

        debug = {
            'candidates': cand_edges,
            'provenance': provenance,
            'scores': scores,
            'parts': parts,
            'allocation': alloc,
            'total_pairs': total_pairs,
            'dp_value': dp_value,
        }
        return self.sources, debug

    def compute_cost(self):
        return len(self.sources) * PAIR_COST


if __name__ == "__main__":
    from network_topology import Topology
    from collections import Counter

    """
    3x3 grid example (length=10 on each edge):
       0 —— 1 —— 2
       |    |    |
       3 —— 4 —— 5
       |    |    |
       6 —— 7 —— 8
    """
    edge_list = [
        (0, 1, 10), (0, 3, 10), (1, 2, 10), (1, 4, 10),
        (2, 5, 10), (3, 4, 10), (3, 6, 10), (4, 7, 10),
        (5, 8, 10), (6, 7, 10), (7, 8, 10)
    ]

    topo = Topology(edge_list)
    users = [1, 6, 5]

    placer = SourcePlacementDP(topo)
    sources, dbg = placer.place_sources_for_request(
        user_set=users,
        cost_budget=12,      # total cost in "pairs" if pair_cost==1
        max_per_edge=5,      # per-edge cap
        K_steiner=3, k_paths=2, weight_attr='length_km',
        w_topo=0.25, w_demand=0.35, w_quality=0.4, w_overlap=0.0,
        p_map=None,          # or provide per-edge success prob: {(u,v): p_e, ...}
        p_op=0.8,
        value_model='prob'   # 'prob' (diminishing) or 'linear'
    )

    print("\n=== Selected sources (edge repeated per pair) ===")
    print(sources)
    print("\n=== Allocation per edge ===")
    for e, y in dbg['allocation'].items():
        if y > 0:
            print(f"{e}: {y} pairs")
    print(f"\nTotal pairs used: {dbg['total_pairs']}, DP objective: {dbg['dp_value']:.4f}")

    source_edge_list = [tuple(sorted(edge)) for edge in sources]
    deployed_dict = dict(Counter(source_edge_list))
    print(deployed_dict)