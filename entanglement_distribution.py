"""
Compute the Distribution Rate (DR) of GHZ states over multiple Monte Carlo trials.
- DR = average number of GHZ states per timeslot
- DR = (1/M) * sum_i(1/T_i) where T_i is the number of timeslots to generate a GHZ
- If a run fails to generate GHZ in 5000 timeslots, it is discarded
- If >5% of runs fail, the whole DR value is discarded
"""


class EntanglementDistribution:
    def __init__(self):
        self.successful_trials = []     # successful trials: list of (time_slot, num_ghz)
        self.failed_trials_num = 0          # number of failed attempts
        self.cost_list = []
        self.cost_budget_list = []

    def record_trial(self, time_to_success, cost, cost_budget, num_ghz=1):
        """Record the number of timeslots needed to generate GHZ in one simulation."""
        if time_to_success == 0:
            self.failed_trials_num += 1
        else:
            self.successful_trials.append((time_to_success, num_ghz))
            self.cost_list.append(cost)
            self.cost_budget_list.append(cost_budget)

    def failure_rate(self):
        total = len(self.successful_trials) + self.failed_trials_num
        return self.failed_trials_num / total if total else 0.0

    def average_dr(self):
        if not self.successful_trials:
            return 0.0
        dr_list_trial = [num_ghz / t for t, num_ghz in self.successful_trials]
        average_dr = sum(dr_list_trial) / len(self.successful_trials)
        return average_dr, dr_list_trial

    def average_cost(self):
        if not self.cost_list:
            return 0.0
        return sum([cost for cost in self.cost_list]) / len(self.cost_list)

    def cost_efficiency_actual(self):
        if not self.successful_trials or not self.cost_list:
            return 0.0

        efficiencies = []
        for i in range(len(self.successful_trials)):
            time, num_ghz = self.successful_trials[i]
            actual_cost = self.cost_list[i]
            if actual_cost > 0:
                efficiencies.append((num_ghz / time) / actual_cost)

        return sum(efficiencies) / len(efficiencies) if efficiencies else 0.0

    def cost_efficiency_budget(self):
        if not self.successful_trials or not self.cost_budget_list:
            return 0.0

        efficiencies = []
        for i in range(len(self.successful_trials)):
            time, num_ghz = self.successful_trials[i]
            budget = self.cost_budget_list[i]
            if budget > 0:
                efficiencies.append((num_ghz / time) / budget)

        return sum(efficiencies) / len(efficiencies) if efficiencies else 0.0

    def is_valid_result(self):
        return self.failure_rate() <= 0.05

    def get_summary_dict(self):
        """Returns a dictionary containing the summary statistics."""
        average_dr_value, dr_list_trial = self.average_dr()
        return {
            "successful_runs": len(self.successful_trials),
            "failed_runs": self.failed_trials_num,
            "failure_rate": self.failure_rate(),
            "average_dr": average_dr_value,
            "dr_list_trial": dr_list_trial,
            "average_cost": self.average_cost(),
            "cost_efficiency_actual": self.cost_efficiency_actual(),
            "cost_efficiency_budget": self.cost_efficiency_budget(),
            "is_valid_result": self.is_valid_result(),
            "successful_times": [t for t, _ in self.successful_trials],
            "successful_ghz_counts": [n for _, n in self.successful_trials],
            "successful_costs": self.cost_list
        }

    def summary(self):
        print(f"  List of successful time slot : {[t for t, _ in self.successful_trials]}")
        print(f"  List of successful ghz count : {[n for _, n in self.successful_trials]}")
        print(f"  List of successful cost :      {self.cost_list}")
        print(f"  cost : {self.average_cost():.6f}")
        print(f"  Total Runs")
        print(f"  Successful Runs : {len(self.successful_trials)}")
        print(f"  Failed Runs     : {self.failed_trials_num}")
        print(f"  Failure Rate    : {self.failure_rate():.2%}")

        if self.is_valid_result():
            print(f"  DR : {self.average_dr():.6f}")
            print(f"  cost_efficiency (actual): {self.cost_efficiency_actual():.6f}")
            print(f"  cost_efficiency (budget): {self.cost_efficiency_budget():.6f}")
        else:
            print("  [!] Too many failures. Discard this datapoint.")
            print(f"  DR : {self.average_dr():.6f}")
            print(f"  cost_efficiency (actual): {self.cost_efficiency_actual():.6f}")
            print(f"  cost_efficiency (budget): {self.cost_efficiency_budget():.6f}")


if __name__ == "__main__":
    import random

    distribution = EntanglementDistribution()
    for _ in range(50):
        t = random.choice([random.randint(1, 500)] * 95 + [600] * 5)  # ~5% failure
        distribution.record_trial(t, cost=5, cost_budget=10, num_ghz=1)

    distribution.summary()
    print("\nSummary Dictionary:")
    print(distribution.get_summary_dict())