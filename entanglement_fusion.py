"""
Performs entanglement fusion on intermediate nodes (non-user) with multiple entangled links.
Each such node fuses its connected users into a GHZ state.
"""

from quantum_network import QuantumNetwork


class EntanglementFusion:
    def __init__(self, network):
        self.network = network
        self.link_manager = network.entanglementlink_manager

    def fuse_users(self, intermediate_node, user_list, current_time, p_op):
        # Step 1: Validate all required links exist and get their IDs
        links_to_fuse = {}
        for u in user_list:
            if u != intermediate_node:
                link_id = self._find_link_id(u, intermediate_node)
                if link_id is None:
                    print(f"[Fusion] Missing Bell pair: {u}–{intermediate_node}")
                    return False
                links_to_fuse[(u, intermediate_node)] = link_id

        # Step 2: Remove old links and memory
        for u in user_list:
            if u != intermediate_node:
                link_id = links_to_fuse.get((u, intermediate_node))
                self._remove_link_by_id(link_id)
                self._release_memory(u, intermediate_node, link_id)
                self._release_memory(intermediate_node, u, link_id)

        # Step 3: Create new GHZ link among users
        self.link_manager.create_link(user_list, p_op=1, gen_time=current_time, length_km=0, attr="Fusion")

        # Step 4: Update memory (pairwise occupancy)
        for u in user_list:
            for v in user_list:
                if u != v:
                    # Note: You need to decide how to represent the new GHZ link in memory
                    # For now, we don't assign it a new link_id as it's a multipartite state.
                    self.network.nodes[u].memory.occupy_memory(v, 0, current_time)

        print(f"[Fusion] GHZ state created among {user_list} via {intermediate_node}")
        return True

    def fuse_users_from_tree(self, user_list, tree_links, current_time, p_op):
        # The paper assumes a GHZ can be formed if a connecting tree exists.
        # This method simulates the process of swapping and fusing along the tree.
        # For simplicity, we assume this is always successful if the links exist.

        # Step 1: Remove all links in the tree and their corresponding memory entries
        for u, v in tree_links:
            link_id = self._find_link_id(u, v)
            if link_id is None:
                 print(f"[Fusion] Missing link {u}-{v} in tree.")
                 return False
            self._remove_link_by_id(link_id)
            self._release_memory(u, v, link_id)
            self._release_memory(v, u, link_id)

        # Step 2: Create a new GHZ link among the users
        self.link_manager.create_link(user_list, p_op=1, gen_time=current_time, length_km=0, attr="Fusion")

        # Step 3: Update memory for the new GHZ state
        for u in user_list:
            for v in user_list:
                if u != v:
                    # Note: You need to decide how to represent the new GHZ link in memory
                    # For now, we don't assign it a new link_id as it's a multipartite state.
                    self.network.nodes[u].memory.occupy_memory(v, 0, current_time)

        print(f"[Fusion] GHZ state created among {user_list} via tree")

        return True

    def _link_exists(self, u, v):
        for link in self.link_manager.links:
            if u in link.nodes and v in link.nodes:
                return True
        return False

    def _find_link_id(self, u, v):
        # Find the ID of a single active link between u and v
        for link in self.link_manager.links:
            if u in link.nodes and v in link.nodes:
                return link.link_id
        return None

    def _remove_link_by_id(self, link_id):
        self.link_manager.remove_link_by_id(link_id)

    def _release_memory(self, node_id, peer_id, link_id):
        mem = self.network.nodes[node_id].memory
        if peer_id in mem.memory_storage:
            mem.memory_storage[peer_id] = [
                (l_id, gen_time, fidelity)
                for l_id, gen_time, fidelity in mem.memory_storage[peer_id]
                if l_id != link_id
            ]
            if not mem.memory_storage[peer_id]:
                del mem.memory_storage[peer_id]

#
# if __name__=="__main__":
#     # A —— D —— C
#     #      |    |
#     #      B ——
#     edge_list = [("A", "D", 10), ("D", "C", 15), ("D", "B", 10), ("B", "C", 20)]
#     net = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=6)
#
#     net.attempt_entanglement("A", "D", p_op=0.9, gen_time=0)
#     net.attempt_entanglement("B", "D", p_op=0.9, gen_time=4)
#     net.attempt_entanglement("C", "D", p_op=0.9, gen_time=4)
#
#     print("\nBefore Fusion")
#     net.show_network_status(current_time=4)
#     fusion = EntanglementFusion(net)
#     fusion.fuse_users("D", ["A", "B", "C"], current_time=5, p_op=0.9)
#     print("\nAfter Fusion")
#     net.show_network_status(current_time=5)