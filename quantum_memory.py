"""
Manage quantum memory with limited number of slots for each node.
Automatically purges expired entanglement links that exceed the decoherence time.

Each entanglement record includes:
    - peer_id: the id of remoted node that build the entanglement link together
    - gen_time: time at which the entanglement was generated
    - fidelity: the current quality of the entangled state (optional)

Memory capacity:
    - size: number of memory slots
    - decoherence_time: max valid lifetime (in time slots)

Key Methods:
    - occupy_memory(peer_id, gen_time, fidelity=1.0)
    - release_memory(current_time)
    - show_memory(current_time)
    - compute_fidelity(gen_time, current_time)

Note:
    After decoherence time, the qubit is assumed to have undergone decoherence and is discarded
    Fidelity decay (exponential or linear) is optional and currently disabled.
"""

import math


class QuantumMemory:
    def __init__(self, node_id, max_per_edge=1, decoherence_time=10, fidelity_decay=None, decay_rate=0.01, ):
        self.node_id = node_id
        self.max_per_edge = max_per_edge
        self.decoherence_time = decoherence_time
        self.fidelity_decay = fidelity_decay
        self.decay_rate = decay_rate
        # Memory is now managed per peer (i.e., per edge)
        # Structure: {peer_id: [(link_id, gen_time), ...]}
        self.memory_storage = {}

    def occupy_memory(self, peer_id, link_id, gen_time, fidelity=1.0):
        # Ensure the peer has an entry in memory_storage
        if peer_id not in self.memory_storage:
            self.memory_storage[peer_id] = []

        # Check if the memory for this specific edge is full
        if len(self.memory_storage[peer_id]) >= self.max_per_edge:
            # print(f"Not enough memory on edge to {peer_id}!")
            return False

        self.memory_storage[peer_id].append((link_id, gen_time, fidelity))
        return True

    def release_memory(self, current_time):
        for peer_id in list(self.memory_storage.keys()):
            # Filter out expired links for each peer
            self.memory_storage[peer_id] = [
                (link_id, gen_time, fidelity)
                for link_id, gen_time, fidelity in self.memory_storage[peer_id]
                if current_time - gen_time < self.decoherence_time
            ]
            # Remove peer from memory_storage if no links are active
            if not self.memory_storage[peer_id]:
                del self.memory_storage[peer_id]

    def show_memory(self, current_time):
        self.release_memory(current_time)
        print(f"  Memory (Max per edge: {self.max_per_edge}) Content at [time slot {current_time}]")
        for peer_id, links in self.memory_storage.items():
            for link_id, gen_time, fidelity in links:
                print(f"    Peer_id: {peer_id}, Link_id: {link_id}, Gen_time: {gen_time}, Fidelity: {fidelity:.2f}")

    def compute_fidelity(self, gen_time, current_time):
        if self.fidelity_decay is None:
            return 1.0
        dt = current_time - gen_time
        if self.fidelity_decay == 'exponential':
            return math.exp(-self.decay_rate * dt)
        elif self.fidelity_decay == 'linear':
            return max(0.0, 1.0 - self.decay_rate * dt)


