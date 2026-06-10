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
import random


class EntanglementSwapping:
    def __init__(self, network):
        self.network = network
        self.link_manager = network.entanglementlink_manager

    def entanglement_swapping(self, path, current_time, p_op=None, q_swap=1.0, q_swap_by_node=None):
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

            q = self._probability_for_node(b, q_swap, q_swap_by_node)
            operation_success = random.random() < q

            # 1. Remove old links A-B and B-C using their unique IDs.
            # A failed Bell measurement still consumes the input qubits.
            self.link_manager.remove_link_by_id(link_ab_id)
            self.link_manager.remove_link_by_id(link_bc_id)

            # 2. Release memory
            self._release_memory(a, b, link_ab_id)
            self._release_memory(b, a, link_ab_id)
            self._release_memory(b, c, link_bc_id)
            self._release_memory(c, b, link_bc_id)

            if not operation_success:
                print(f"[Swapping] Failed at node {b}: {a} <-> {c} (q_swap={q:.4f})")
                return False

            # 3. Create new entanglement link A-C. Occupy memory of nodeA and nodeC
            success, new_link_id = self.network.attempt_entanglement(
                a,
                c,
                p_op=1,
                gen_time=current_time,
                attr="Swapping",
                state_type="SWAPPED_BELL",
                operation="SWAPPING",
                operation_node=b,
                parent_link_ids=[link_ab_id, link_bc_id],
            )

            if not success:
                return False

            print(f"[Swapping] Performed swapping at node {b}: {a} <-> {c} (via {b})")

            path_copy.pop(1)  # remove B

        return True

    def _link_exists(self, u, v):
        for link in self.link_manager.links:
            if len(link.nodes) == 2 and u in link.nodes and v in link.nodes:
                return True
        return False

    def _find_link_id(self, u, v):
        # Find the ID of a single active link between u and v
        for link in self.link_manager.links:
            if len(link.nodes) == 2 and u in link.nodes and v in link.nodes:
                return link.link_id
        return None

    def _probability_for_node(self, node_id, q_swap, q_swap_by_node):
        if q_swap_by_node is not None:
            return float(q_swap_by_node.get(node_id, q_swap))
        return float(q_swap)

    def _release_memory(self, node_id, peer_id, link_id):
        self.network.nodes[node_id].memory.release_by_link_id(link_id, peer_id=peer_id)

#
def main():
    edge_list = [("A", "B", 10), ("B", "C", 10)]

    net = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=6)
    net.attempt_entanglement("A", "B", gen_time=0, p_op=1.0, flag=True)
    net.attempt_entanglement("B", "C", gen_time=0, p_op=1.0, flag=True)
    swapping = EntanglementSwapping(net)
    assert swapping.entanglement_swapping(["A", "B", "C"], current_time=1, q_swap=1.0)
    assert swapping._link_exists("A", "C")
    assert "A" not in net.nodes["B"].memory.memory_storage
    assert "C" not in net.nodes["B"].memory.memory_storage
    swapped_link = [link for link in net.entanglementlink_manager.links if set(link.nodes) == {"A", "C"}][0]
    assert swapped_link.state_type == "SWAPPED_BELL"
    assert swapped_link.operation_node == "B"

    net = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=6)
    net.attempt_entanglement("A", "B", gen_time=0, p_op=1.0, flag=True)
    net.attempt_entanglement("B", "C", gen_time=0, p_op=1.0, flag=True)
    swapping = EntanglementSwapping(net)
    assert not swapping.entanglement_swapping(["A", "B", "C"], current_time=1, q_swap=0.0)
    assert not net.entanglementlink_manager.links
    assert net.nodes["A"].get_memory_usage() == 0
    assert net.nodes["B"].get_memory_usage() == 0
    assert net.nodes["C"].get_memory_usage() == 0

    print("EntanglementSwapping main test passed.")


if __name__ == "__main__":
    main()
