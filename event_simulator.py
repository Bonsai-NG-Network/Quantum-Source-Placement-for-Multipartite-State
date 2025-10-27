import random
import networkx as nx
from network_request import RequestGenerator
from quantum_network import QuantumNetwork
from singlepath_routing import SPEntanglementRouting
from multipath_routing import MPGreedyRouting, MPCooperativeRouting, MPPackingRouting
from entanglement_swapping import EntanglementSwapping
from entanglement_fusion import EntanglementFusion
from entanglement_distribution import EntanglementDistribution
from quantum_source_placement import SourcePlacement
from quantum_source_placement_dp import SourcePlacementDP
from quantum_source_placement_backup import SourcePlacementBackup
from entanglement_link import EntanglementLink
from collections import Counter


class EventSimulator:
    def __init__(self, edge_list, max_per_edge, p_op, decoherence_time, num_users=3, max_timeslot=500):
    # def __init__(self, length_network, width_network, edge_length_km, max_per_edge, p_op, decoherence_time, num_users=3, max_timeslot=500):
        self.p_op = p_op
        self.num_users = num_users
        self.max_timeslot = max_timeslot
        self.max_per_edge = max_per_edge
        self.network = QuantumNetwork(edge_list=edge_list, max_per_edge=self.max_per_edge,
                                      decoherence_time=decoherence_time)
        # self.network = QuantumNetwork(length_network=length_network, width_network=width_network,
        #                               edge_length_km=edge_length_km,
        #                               max_per_edge=self.max_per_edge, decoherence_time=decoherence_time)
        self.link_manager = self.network.entanglementlink_manager
        self.topo = self.network.topo
        self.user_gen = RequestGenerator(self.topo.get_nodes())
        self.swapping = EntanglementSwapping(self.network)
        self.fusion = EntanglementFusion(self.network)
        # self.DR = EntanglementDistribution()

    def select_center_node(self, user_set, edge_probs, deployed_sources):
        best_v = None
        best_dr = 0.0

        def edge_cap(u, w):
            return deployed_sources.get(tuple(sorted((u, w))), 0)

        for v in self.topo.graph.nodes():
            # if self.topo.graph.degree[v] < len(user_set):
            #     continue

            required_flow = (len(user_set) - 1) if (v in user_set) else len(user_set)

            # 统计与 v 相邻且已部署的总容量（允许同一条边容量>1）
            incident_capacity = 0
            for n in self.topo.graph.neighbors(v):
                incident_capacity += edge_cap(v, n)

            if incident_capacity < required_flow:
                continue

            G_cap = nx.DiGraph()
            for (u, w) in self.topo.graph.edges():
                edge_key = tuple(sorted((u, w)))
                cap = deployed_sources.get(edge_key, 0)
                if cap > 0:
                    G_cap.add_edge(u, w, capacity=cap)
                    G_cap.add_edge(w, u, capacity=cap)

            if v not in G_cap.nodes:
                continue

            T = "super_sink"
            for u in user_set:
                if u == v:
                    continue
                G_cap.add_edge(u, T, capacity=1)

            try:
                flow_value, flow_dict = nx.maximum_flow(G_cap, v, T)
            except nx.NetworkXError:
                continue

            if flow_value < required_flow:
                continue

            dr = 1.0
            for u in user_set:
                if u == v:
                    continue
                try:
                    path = nx.shortest_path(self.topo.graph, source=v, target=u)
                    for i in range(len(path) - 1):
                        e = tuple(sorted((path[i], path[i + 1])))
                        if e not in edge_probs:
                            dr = 0.0
                            break
                        dr *= edge_probs[e]
                except nx.NetworkXNoPath:
                    dr = 0.0
                    break

            if dr > best_dr:
                best_v = v
                best_dr = dr

        print(f"[Select Center] Chosen center node: {best_v}, with estimated DR_SP = {best_dr:.4f}")
        return best_v, best_dr

    def get_shortest_paths_SP(self, v, user_set):
        paths = {}
        # print(f"\n[Routing] Computing shortest paths from center node '{v}' to users {user_set}:")
        for s in user_set:
            if s == v:
                continue
            try:
                path = nx.shortest_path(self.topo.graph, source=v, target=s)
                paths[s] = path
                # print(f"  Path to {s}: {path}")
            except nx.NetworkXNoPath:
                paths[s] = []
                # print(f"  No path to {s}")
        return paths

    # only used for selecting center node
    def compute_dr_sp(self, paths, subgraph):
        dr = 1.0
        for path in paths.values():
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                if subgraph.has_edge(u, v):
                    p_e = subgraph.edges[u, v].get("p_e", 1.0)
                    dr *= p_e
                else:
                    return 0.0
        return dr

    def run_single_trial_SP(self, user_set, p_op, edge_probs, deployed_sources):
        """
        Run simulation until GHZ is formed for the given user set or until max timeslot.
        Returns number of time slots required or None if failed.
        """
        G_prime = self.link_manager.get_subgraph(current_time=1)

        # vc: The selected center node vc remains fixed
        vc, best_dr = self.select_center_node(user_set, edge_probs, deployed_sources)
        if vc is None:
            print(f"\n[Routing] Final selected center: {vc}")
            return 0, 0

        # paths: the routing solution computed for the selected central node remains fixed
        paths = self.get_shortest_paths_SP(vc, user_set)
        print(f"\n[Routing] Final selected center: {vc}")
        for s, path in paths.items():
            print(f"  {vc} -> {s}: {path}")

        SProuting = SPEntanglementRouting(self.network, user_set, p_op)
        time_to_success, num_ghz = SProuting.sp_routing(vc, paths, self.max_timeslot, deployed_sources)
        return time_to_success, num_ghz

    def run_single_trial_MPG(self, user_set, p_op, edge_probs, deployed_sources):
        G_prime = self.link_manager.get_subgraph(current_time=1)

        # vc: The selected center node vc remains fixed
        vc, best_dr = self.select_center_node(user_set, edge_probs, deployed_sources)
        if vc is None:
            print(f"\n[Routing] Final selected center: {vc}")
            return 0

        # paths: the routing solution computed for the selected central node changes with subgraph
        MPGrouting = MPGreedyRouting(self.network, user_set, p_op)
        time_to_success = MPGrouting.mp_greedy_routing(vc, self.max_timeslot, deployed_sources)
        return time_to_success

    def run_single_trial_MPC(self, user_set, p_op, deployed_sources):
        """
        Run simulation until GHZ is formed for the given user set or until max timeslot
        using the MP-C protocol.
        """
        # Unlike SP/MPG, MPC does not require a pre-selected center node.
        # Source placement is still based on the Steiner tree heuristic.
        MPCrouting = MPCooperativeRouting(self.network, user_set, p_op)
        time_to_success = MPCrouting.mpc_routing(self.max_timeslot, deployed_sources)
        return time_to_success

    def run_single_trial_MPP(self, user_set, p_op, deployed_sources):
        """
        Run simulation until GHZ is formed for the given user set or until max timeslot
        using the MP-P protocol.
        """
        # MPP also does not require a pre-selected center node.
        MPProuting = MPPackingRouting(self.network, user_set, p_op)
        time_to_success, num_ghz = MPProuting.mpp_routing(self.max_timeslot, deployed_sources)
        return time_to_success, num_ghz

    def run_trials(self, seed, user_sets, routing_method, source_method, dr_object, cost_budget):
        if seed is not None:
            random.seed(seed)

        all_deployed_dicts = []

        for i, user_set in enumerate(user_sets):
            trial_num = i + 1
            print("\n" + "=" * 100)
            print(f"[Trial {trial_num} for {routing_method}] Running...")
            print(f"[user_set] {user_set}")

            self.network.reset()
            if source_method == "NOP":
                source = SourcePlacement(self.topo)
                sources = source.place_sources_for_request(user_set, method=source_method, cost_budget=cost_budget,
                                                           max_per_edge=self.max_per_edge)
                cost = source.compute_cost()

            elif source_method == "OP":
                placer = SourcePlacementDP(self.topo)
                sources, dbg = placer.place_sources_for_request(
                    user_set=user_set,
                    cost_budget=cost_budget,  # total cost in "pairs" if pair_cost==1
                    max_per_edge=self.max_per_edge,  # per-edge cap
                    K_steiner=4, k_paths=2, weight_attr='length_km',
                    w_topo=0.25, w_demand=0.35, w_quality=0.4, w_overlap=0.0,
                    p_map=None,  # or provide per-edge success prob: {(u,v): p_e, ...}
                    p_op=self.p_op,
                    value_model='prob'  # 'prob' (diminishing) or 'linear'
                )
                cost = placer.compute_cost()
            elif source_method == "OP_BP":
                sp_backup = SourcePlacementBackup(self.topo)
                sources = sp_backup.place_sources_for_request(
                    user_set=user_set,
                    method="mt_overlap",
                    cost_budget=cost_budget,
                    max_per_edge=self.max_per_edge,
                    k_trees=3,
                    p_op=self.p_op,
                    loss_coef_dB_per_km=0.2,
                    seed=2,
                )
                cost = sp_backup.compute_cost()
            else:
                raise ValueError(f"Unknown source_method: {source_method}")

            source_edge_list = [tuple(sorted(edge)) for edge in sources]
            deployed_dict = dict(Counter(source_edge_list))

            all_deployed_dicts.append(deployed_dict)

            edge_probs = {}   # used for choosing the center node
            for (u, v) in sources:
                length_km = self.topo.graph[u][v].get("length", 1)
                temp_link = EntanglementLink(
                    link_id=f"{u}-{v}",
                    nodes=[u, v],
                    gen_time=0,
                    length_km=length_km,
                    p_op=self.p_op,
                    loss_coef_dB_per_km=0.2
                )
                edge_probs[(u, v)] = temp_link.p_e

            for u, v in sources:
                self.network.attempt_entanglement(u, v, p_op=self.p_op, gen_time=0)
            self.network.show_network_status(current_time=0)

            num_ghz = 1  # Default for SP, MPG, MPC

            if routing_method == 'PR':
                time_to_success, num_ghz = self.run_single_trial_SP(user_set, self.p_op, edge_probs, deployed_dict)
            elif routing_method == 'MPG':
                time_to_success = self.run_single_trial_MPG(user_set, self.p_op, edge_probs, deployed_dict)
            elif routing_method == 'MPC':
                time_to_success = self.run_single_trial_MPC(user_set, self.p_op, deployed_dict)
            elif routing_method == 'RR':
                time_to_success, num_ghz = self.run_single_trial_MPP(user_set, self.p_op, deployed_dict)
            else:
                raise ValueError(f"Unknown routing_method: {routing_method}")

            if time_to_success:
                print(f"[Trial {trial_num}] ✅ GHZ generated in {time_to_success} time slots for {routing_method}.")
            else:
                print(f"[Trial {trial_num}] ❌ Failed to generate GHZ for {routing_method} within time limit.")

            dr_object.record_trial(time_to_success, cost, cost_budget, num_ghz)

        return all_deployed_dicts


if __name__ == "__main__":
    """
        0 —— 1 —— 2
        |    |    |
        3 —— 4 —— 5
        |    |    |
        6 —— 7 —— 8
    """

    m = 5
    length = 10
    edge_list = []
    for row in range(m):
        for col in range(m):
            node = row * m + col
            # Right neighbor
            if col < m - 1:
                right = node + 1
                edge_list.append((node, right, length))
            # Bottom neighbor
            if row < m - 1:
                down = node + m
                edge_list.append((node, down, length))

    LENGTH_NETWORK = 3
    WIDTH_NETWORK = 3
    EDGE_LENGTH_KM = 1

    RANDOM_SEED = 1
    NUM_TRIALS = 100
    P_OP = 0.9
    NUM_USERS = 3
    DECOHERENCE_TIME = 1
    MAX_TIMEESLOT_PER_TRIAL = 300
    MAX_PER_EDGE = 3
    # SOURCE_METHOD = "all_edges"
    # COST_BUDGET = None
    SOURCE_METHOD = "steiner_tree"
    COST_BUDGET = 18

    simulator = EventSimulator(edge_list, num_users=NUM_USERS, p_op=P_OP, max_per_edge=MAX_PER_EDGE,
                               decoherence_time=DECOHERENCE_TIME, max_timeslot=MAX_TIMEESLOT_PER_TRIAL)
    #
    # simulator = EventSimulator(length_network=LENGTH_NETWORK, width_network=WIDTH_NETWORK, edge_length_km=EDGE_LENGTH_KM,
    #                            num_users=NUM_USERS, p_op=P_OP, max_per_edge=MAX_PER_EDGE,
    #                            decoherence_time=DECOHERENCE_TIME, max_timeslot=MAX_TIMEESLOT_PER_TRIAL)

    dr_sp = EntanglementDistribution()
    dr_mpg = EntanglementDistribution()
    dr_mpc = EntanglementDistribution()
    dr_mpp = EntanglementDistribution()

    print(f"Generating {NUM_TRIALS} user sets for the experiment...")
    random.seed(RANDOM_SEED)
    user_sets_list = [simulator.user_gen.random_users(k=NUM_USERS) for _ in range(NUM_TRIALS)]
    # user_sets_list = [[(0, 0), (0, 4), (4, 0), (4, 4)]]

    print("\nGenerated User Sets List for all trials:")
    print(user_sets_list)

    # --- Run Simulations ---
    # Run Single-Path trials
    print("\n" + "#" * 60)
    print("###   STARTING SINGLE-PATH (SP) ROUTING SIMULATION   ###")
    print("#" * 60)
    simulator.run_trials(user_sets=user_sets_list, routing_method='SP', source_method=SOURCE_METHOD, seed=RANDOM_SEED,
                         dr_object=dr_sp, cost_budget=COST_BUDGET)

    # Run Multi-Path Greedy trials
    print("\n" + "#" * 60)
    print("###   STARTING MULTI-PATH GREEDY (MPG) ROUTING SIMULATION   ###")
    print("#" * 60)
    simulator.run_trials(user_sets=user_sets_list, routing_method='MPG', source_method=SOURCE_METHOD, seed=RANDOM_SEED,
                         dr_object=dr_mpg, cost_budget=COST_BUDGET)

    # Run Multi-Path Cooperative trials
    print("\n" + "#" * 60)
    print("###   STARTING MULTI-PATH COOPERATIVE (MPC) ROUTING SIMULATION   ###")
    print("#" * 60)
    simulator.run_trials(user_sets=user_sets_list, routing_method='MPC', source_method=SOURCE_METHOD, seed=RANDOM_SEED,
                         dr_object=dr_mpc, cost_budget=COST_BUDGET)

    # Run Multi-Path Packing trials
    print("\n" + "#" * 60)
    print("###   STARTING MULTI-PATH PACKING (MPP) ROUTING SIMULATION   ###")
    print("#" * 60)
    simulator.run_trials(user_sets=user_sets_list, routing_method='MPP', source_method=SOURCE_METHOD, seed=RANDOM_SEED,
                         dr_object=dr_mpp, cost_budget=COST_BUDGET)

    # --- Final Summary ---
    print("\n\n" + "*" * 50)
    print("********** FINAL SIMULATION SUMMARY   **********")
    print("*" * 50)

    print("\n--- DR Summary for SinglePath (SP) Routing ---")
    dr_sp.summary()

    print("\n--- DR Summary for MultiPath Greedy (MPG) Routing ---")
    dr_mpg.summary()

    print("\n--- DR Summary for MultiPath Cooperative (MPC) Routing ---")
    dr_mpc.summary()

    print("\n--- DR Summary for MultiPath Packing (MPP) Routing ---")
    dr_mpp.summary()
