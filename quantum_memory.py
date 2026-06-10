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

    @staticmethod
    def ghz_memory_key(ghz_link_id):
        return ("GHZ", ghz_link_id)

    def occupy_ghz_memory(self, ghz_link_id, gen_time, fidelity=1.0):
        return self.occupy_memory(
            peer_id=self.ghz_memory_key(ghz_link_id),
            link_id=ghz_link_id,
            gen_time=gen_time,
            fidelity=fidelity,
        )

    def release_by_link_id(self, link_id, peer_id=None):
        peer_ids = [peer_id] if peer_id is not None else list(self.memory_storage.keys())
        released = 0

        for key in peer_ids:
            if key not in self.memory_storage:
                continue

            before = len(self.memory_storage[key])
            self.memory_storage[key] = [
                (stored_link_id, gen_time, fidelity)
                for stored_link_id, gen_time, fidelity in self.memory_storage[key]
                if stored_link_id != link_id
            ]
            released += before - len(self.memory_storage[key])

            if not self.memory_storage[key]:
                del self.memory_storage[key]

        return released

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


def main():
    memory = QuantumMemory(node_id="A", max_per_edge=2, decoherence_time=10)

    assert memory.occupy_memory("B", "bell-1", gen_time=0)
    assert memory.occupy_memory("B", "bell-2", gen_time=0)
    assert not memory.occupy_memory("B", "bell-3", gen_time=0)
    assert memory.release_by_link_id("bell-1", peer_id="B") == 1
    assert len(memory.memory_storage["B"]) == 1

    assert memory.occupy_ghz_memory("ghz-1", gen_time=1)
    ghz_key = QuantumMemory.ghz_memory_key("ghz-1")
    assert ghz_key in memory.memory_storage
    assert memory.release_by_link_id("ghz-1") == 1
    assert ghz_key not in memory.memory_storage

    print("QuantumMemory main test passed.")


if __name__ == "__main__":
    main()


