"""
Node A --- Node B --- Node C
If A-B and B-C have entanglement links, then B performs Bell measurement (swapping)
Each swap replaces two links with one, and updates node memory & link manager.
"""

"""
Node A --- Node B --- Node C
If A-B and B-C have entanglement links, then B performs Bell measurement (swapping)
Each swap replaces two links with one, and updates node memory & link manager.
"""

from quantum_network import QuantumNetwork


class EntanglementSwapping:
    def __init__(self, network):
        self.network = network
        self.link_manager = network.entanglementlink_manager

    def entanglement_swapping(self, path, current_time, p_op):
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if not self._link_exists(u, v):
                print(f"[Swapping] Missing entanglement link between {u} and {v}")
                return False

        path_copy = path[:]

        while len(path_copy) >= 3:
            a = path_copy[0]
            b = path_copy[1]
            c = path_copy[2]

            # Find the specific link IDs to be removed
            link_ab_id = self._find_link_id(a, b)
            link_bc_id = self._find_link_id(b, c)

            if link_ab_id is None or link_bc_id is None:
                print(f"[Swapping] Could not find specific links for swapping at node {b}")
                return False

            # 1. Remove old links A-B and B-C using their unique IDs
            self.link_manager.remove_link_by_id(link_ab_id)
            self.link_manager.remove_link_by_id(link_bc_id)

            # 2. Release memory
            self._release_memory(a, b, link_ab_id)
            self._release_memory(b, a, link_ab_id)
            self._release_memory(b, c, link_bc_id)
            self._release_memory(c, b, link_bc_id)

            # 3. Create new entanglement link A-C. Occupy memory of nodeA and nodeC
            success, new_link_id = self.network.attempt_entanglement(a, c, p_op=1, gen_time=current_time,
                                                                     attr="Swapping")

            if not success:
                return False

            print(f"[Swapping] Performed swapping at node {b}: {a} <-> {c} (via {b})")

            path_copy.pop(1)  # remove B

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
# if __name__ == "__main__":
#     # A - B - C
#     edge_list = [("A", "B", 10), ("B", "C", 15), ("C", "D", 10)]
#     net = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=6)
#
#     net.attempt_entanglement("A", "B", gen_time=0, p_op=0.9)
#     net.attempt_entanglement("B", "C", gen_time=4, p_op=0.9)
#     net.attempt_entanglement("C", "D", gen_time=4, p_op=0.9)
#
#     print("\nBefore Swapping")
#     net.show_network_status(current_time=4)
#     swapping = EntanglementSwapping(net)
#     success = swapping.entanglement_swapping(path=["A", "B", "C", "D"], current_time=5, p_op=0.9)
#     if success:
#         print(f"\nAfter Successful Swapping")
#     else:
#         print(f"\nAfter Failed Swapping")
#     net.show_network_status(current_time=5)