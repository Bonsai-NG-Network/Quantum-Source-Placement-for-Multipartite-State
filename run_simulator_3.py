import random
import matplotlib.pyplot as plt
import os
import pandas as pd
from datetime import datetime
from event_simulator import EventSimulator
from entanglement_distribution import EntanglementDistribution
import numpy as np
from network_request import RequestGenerator
"""
    柱状图+置信空间
"""


# --- Simulation Parameters ---
RANDOM_SEED = 1
NUM_TRIALS = 100
MAX_TIMEESLOT_PER_TRIAL = 80

DECOHERENCE_TIME = 1
MAX_PER_EDGE = 5
EDGE_LENGTH_KM = 10


# Fig.1 基础参数
LENGTH_NETWORK_PROTOCOLS_1 = 5
WIDTH_NETWORK_PROTOCOLS_1 = 5
NUM_USERS_PROTOCOLS_1 = 3
OP_PROTOCOLS_1 = 0.8

# Fig.2（横轴是 p_op）
LENGTH_NETWORK_OP_2 = 5
WIDTH_NETWORK_OP_2 = 5
NUM_USERS_OP_2 = 3
P_OP_LIST_2 = [0.5, 0.6, 0.7, 0.8, 0.9]
# P_OP_LIST_2 = np.arange(0.5, 1.0, 0.1)

# Fig.3（横轴是 Network Size）
NET_SIZES_3 = [(3, 3), (4, 4), (5, 5)]
NUM_USERS_NET_SIZE_3 = 3
OP_NET_SIZE_3 = 0.6

# Fig.4（横轴是 #Users）
LENGTH_NETWORK_USER_4 = 3
WIDTH_NETWORK_USER_4 = 3
NUM_USERS_LIST_4 = [3, 4, 5]
OP_USER_4 = 0.8

# Fig.5（横轴是 Decoherence Time）
LENGTH_NETWORK_DT_5 = 5
WIDTH_NETWORK_DT_5 = 5
NUM_USERS_DT_5 = 3
OP_DT_5 = 0.8
DT_LIST_5 = [1, 2, 3, 4]


EDGE_LIST = [
    (0, 1, 10), (1, 2, 10),
    (3, 4, 10), (4, 5, 10),
    (6, 7, 10), (7, 8, 10),

    (0, 3, 10), (1, 4, 10), (2, 5, 10),
    (3, 6, 10), (4, 7, 10), (5, 8, 10),
]


def nodes_from_edge_list(edge_list):
    nodes = set()
    for u, v, *rest in edge_list:
        nodes.add(u); nodes.add(v)
    return sorted(nodes)


# FIXED_BUDGET = 21
# COST_BUDGETS = list(range(21, 40, 4))

FIXED_BUDGET = 12
COST_BUDGETS = list(range(12, 32, 4))


def export_run_parameters(writer):
    """Write key run parameters into a dedicated Excel sheet."""
    rows = [
        ("RANDOM_SEED", RANDOM_SEED),
        ("NUM_TRIALS", NUM_TRIALS),
        ("MAX_TIMEESLOT_PER_TRIAL", MAX_TIMEESLOT_PER_TRIAL),
        ("DECOHERENCE_TIME(default)", DECOHERENCE_TIME),
        ("MAX_PER_EDGE", MAX_PER_EDGE),
        ("EDGE_LENGTH_KM", EDGE_LENGTH_KM),
        ("COST_BUDGETS", COST_BUDGETS),
        ("FIXED_BUDGET(for Fig.2–5)", FIXED_BUDGET),

        # Fig.1
        ("LENGTH_NETWORK_PROTOCOLS_1", LENGTH_NETWORK_PROTOCOLS_1, "Fig.1"),
        ("WIDTH_NETWORK_PROTOCOLS_1",  WIDTH_NETWORK_PROTOCOLS_1,  "Fig.1"),
        ("NUM_USERS_PROTOCOLS_1",      NUM_USERS_PROTOCOLS_1,      "Fig.1"),
        ("OP_PROTOCOLS_1",             OP_PROTOCOLS_1,             "Fig.1"),

        # Fig.2
        ("LENGTH_NETWORK_OP_2",        LENGTH_NETWORK_OP_2,        "Fig.2"),
        ("WIDTH_NETWORK_OP_2",         WIDTH_NETWORK_OP_2,         "Fig.2"),
        ("NUM_USERS_OP_2",             NUM_USERS_OP_2,             "Fig.2"),
        ("P_OP_LIST_2",                P_OP_LIST_2,                "Fig.2"),

        # Fig.3
        ("NET_SIZES_3",                NET_SIZES_3,                "Fig.3"),
        ("NUM_USERS_NET_SIZE_3",       NUM_USERS_NET_SIZE_3,       "Fig.3"),
        ("OP_NET_SIZE_3",              OP_NET_SIZE_3,              "Fig.3"),

        # Fig.4
        ("LENGTH_NETWORK_USER_4",      LENGTH_NETWORK_USER_4,      "Fig.4"),
        ("WIDTH_NETWORK_USER_4",       WIDTH_NETWORK_USER_4,       "Fig.4"),
        ("NUM_USERS_LIST_4",           NUM_USERS_LIST_4,           "Fig.4"),
        ("OP_USER_4",                  OP_USER_4,                  "Fig.4"),

        # Fig.5
        ("LENGTH_NETWORK_DT_5",        LENGTH_NETWORK_DT_5,        "Fig.5"),
        ("WIDTH_NETWORK_DT_5",         WIDTH_NETWORK_DT_5,         "Fig.5"),
        ("NUM_USERS_DT_5",             NUM_USERS_DT_5,             "Fig.5"),
        ("OP_DT_5",                    OP_DT_5,                    "Fig.5"),
        ("DT_LIST_5",                  DT_LIST_5,                  "Fig.5"),
    ]
    df = pd.DataFrame(rows, columns=["Parameter", "Value", "Used In"])
    df.to_excel(writer, sheet_name="Run_Params", index=False)


def mean_and_ci95(samples):
    """
    Return (mean, half_CI95) for a list/array of samples.
    Uses normal approx: mean ± 1.96 * std/sqrt(n).
    """
    import numpy as np
    arr = np.asarray(samples, dtype=float)
    n = len(arr)
    if n == 0:
        return 0.0, 0.0
    mean = float(np.mean(arr))
    if n == 1:
        return mean, 0.0
    std = float(np.std(arr, ddof=1))
    half = 1.96 * std / (n ** 0.5)
    return mean, half


def run_and_get_metrics(params, user_sets_list):
    simulator = EventSimulator(
        # length_network=params['length_network'],
        # width_network=params['width_network'],
        # edge_length_km=EDGE_LENGTH_KM,
        edge_list=EDGE_LIST,
        num_users=params['num_users'],
        p_op=params['p_op'],
        max_per_edge=MAX_PER_EDGE,
        decoherence_time=params.get('decoherence_time', DECOHERENCE_TIME),
        max_timeslot=MAX_TIMEESLOT_PER_TRIAL
    )

    dr_object = EntanglementDistribution()
    random.seed(RANDOM_SEED)

    deployed_dicts_per_trial = simulator.run_trials(
        user_sets=user_sets_list,
        routing_method=params['routing_method'],
        source_method=params['source_method'],
        seed=RANDOM_SEED,
        dr_object=dr_object,
        cost_budget=params['cost_budget']
    )

    summary = dr_object.get_summary_dict()
    summary['deployed_dicts'] = str(deployed_dicts_per_trial)

    dr_samples = summary.get('dr_list_trial', [])
    dr_mean, dr_ci = mean_and_ci95(dr_samples)

    # 检查均值是否与 summary['average_dr'] 一致（仅用于 sanity check）
    if abs(dr_mean - summary.get('average_dr', dr_mean)) > 1e-9:
        print(f"[WARN] average_dr mismatch: {summary['average_dr']:.6f} vs recomputed {dr_mean:.6f}")

    summary['ci95_halfwidth'] = dr_ci

    return summary


# ---------- Styling helpers ----------
def line_style_for(method: str) -> str:
    return '-' if method == 'OP' else '--'


def bar_style_kwargs(method: str, color):
    if method == 'PR':
        return dict(color=color, alpha=0.75)
    else:
        return dict(facecolor='none', edgecolor=color, hatch='//', linewidth=1.5)


def place_legend_inside(ax, loc='upper left', bbox_to_anchor=(0.02, 0.98)):
    return ax.legend(
        loc=loc,
        bbox_to_anchor=bbox_to_anchor,
        borderaxespad=0.4, frameon=True, framealpha=0.9,
        prop={'size': 18},
        ncol=2
    )


# ---------- Generic plotting helpers ----------
def create_dr_plot(title, x_label, figuresize=(11, 6.5)):
    fig, ax = plt.subplots(figsize=figuresize)
    ax.set_xlabel(x_label, fontsize=28)
    ax.set_ylabel('Distribution Rate', fontsize=28)
    ax.grid(True, which='both', linestyle='--', linewidth=1, axis='y')
    # ax.set_title(title, fontsize=28)
    ax.tick_params(axis='both', labelsize=28)
    return fig, ax


def create_combo_plot(title, x_label):
    fig, ax1 = plt.subplots(figsize=(11, 6.5))
    ax2 = ax1.twinx()
    ax1.set_xlabel(x_label, fontsize=28)
    ax1.set_ylabel('Cost-Efficiency (CE)', color='tab:blue', fontsize=28)
    ax2.set_ylabel('Distribution Rate (DR)', color='tab:red', fontsize=28)
    ax1.tick_params(axis='y', labelcolor='tab:blue')
    ax2.tick_params(axis='y', labelcolor='tab:red')
    ax1.grid(True, which='both', linestyle='--', linewidth=0.5, axis='y')
    ax1.set_title(title, fontsize=28)
    return fig, ax1, ax2


def plot_grouped_bars(ax, results_dict, x_positions, x_ticklabels, group_key_fn):
    """
    绘制“同色不同表现”的分组柱状图。
    x_positions: ndarray (长度 = x 类别数，等距)
    x_ticklabels: 刻度文本
    """
    groups = []
    for label in results_dict.keys():
        g = group_key_fn(label)
        if g not in groups:
            groups.append(g)
    group_to_color_idx = {g: i for i, g in enumerate(groups)}

    num_series = len(results_dict)
    total_width = 0.6  # 柱群总宽度
    bar_width = total_width / num_series

    for i, (label, data) in enumerate(results_dict.items()):
        try:
            _key, key_ = label.split('-', 1)
        except ValueError:
            key_ = 'RR'
        color = plt.cm.get_cmap('tab10')(group_to_color_idx[group_key_fn(label)] % 10)

        y_means = data['dr']
        y_errs = data.get('ci', None)

        x = x_positions + (i - (num_series - 1) / 2) * bar_width
        ax.bar(
            x, y_means, width=bar_width, label=label,
            yerr=y_errs, capsize=3,
            error_kw={'elinewidth': 1.2, 'alpha': 0.9},
            **bar_style_kwargs(key_, color)
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_ticklabels)


# ---------- 折线图 ----------
def color_for_method(method: str):
    # 让同一“Source Method”用同色，不同Protocol用不同线型
    palette = {'NOP': 0, 'OP': 1, 'OP_BP': 2}
    return plt.cm.get_cmap('tab10')(palette.get(method, 0) % 10)


def linestyle_for_protocol(protocol: str):
    # SR 实线，DR 虚线（可继续扩展 MP 等）
    return '-' if protocol == 'PR' else '--'

# --- 简单平滑工具（有 scipy 用 savgol，没有就移动平均） ---
def _smooth_array(y, window=9, poly=3):
    import numpy as _np
    try:
        from scipy.signal import savgol_filter
        if window % 2 == 0:  # savgol 需要奇数窗口
            window += 1
        return savgol_filter(y, window_length=min(window, max(3, len(y)//2*2+1)), polyorder=min(poly, 3))
    except Exception:
        # 退化为移动平均
        w = max(3, window | 1)  # 保证奇数
        pad = w // 2
        ypad = _np.r_[y[pad:0:-1], y, y[-2:-pad-2:-1]]  # 反射填充
        ker = _np.ones(w) / w
        sm = _np.convolve(ypad, ker, mode='valid')
        return sm[:len(y)]


def plot_grouped_lines(ax, results_dict, x_vals, dense_factor=10, smooth_window=9, smooth_poly=3):
    """
    将 results_dict 里的序列画成折线：
      label 形如 'NOP-SR', 'OP-DR' ...
      每条曲线使用：同 method 同色，不同 protocol 不同线型
    """
    # handles = []
    # for label, data in results_dict.items():
    #     try:
    #         method, protocol = label.split('-', 1)
    #     except ValueError:
    #         method, protocol = label, 'PR'
    #     y = data['dr']
    #     h, = ax.plot(
    #         x_vals, y,
    #         linestyle=linestyle_for_protocol(protocol),
    #         marker='o', markersize=5, linewidth=2.0,
    #         label=label, color=color_for_method(method)
    #     )
    #     handles.append(h)

    handles = []

    # 预生成稠密的 x
    x_vals = np.asarray(x_vals, dtype=float)
    x_dense = np.linspace(x_vals.min(), x_vals.max(), max(len(x_vals)*dense_factor, len(x_vals)))

    for label, data in results_dict.items():
        try:
            method, protocol = label.split('-', 1)
        except ValueError:
            method, protocol = label, 'PR'

        y = np.asarray(data['dr'], dtype=float)

        # 1) 线性插值到稠密 x
        y_dense = np.interp(x_dense, x_vals, y)

        # 2) 平滑
        y_dense_smooth = _smooth_array(y_dense, window=smooth_window, poly=smooth_poly)

        # 3) 画“平滑连线 + 原始圆点”
        h, = ax.plot(
            x_dense, y_dense_smooth,
            linestyle=linestyle_for_protocol(protocol),
            linewidth=2.0,
            color=color_for_method(method),
            label=label
        )
        ax.plot(
            x_vals, y,
            linestyle='None', marker='o', markersize=5,
            color=color_for_method(method)
        )
        handles.append(h)

    ticks = np.linspace(0.5, 0.9, 5)
    ax.set_xticks(ticks)
    ax.set_xticklabels([f'{t:.1f}' for t in ticks])

    return handles


# ===================== FIGURE 1 =====================
def plot_protocols_vs_budget(excel_writer, output_dir, user_sets_list):
    """Fig.1: Protocols vs Budget (x = budget)"""
    print("\n" + "#" * 60)
    print("###   GENERATING FIG.1: Protocols vs Budget   ###")
    print("#" * 60)

    protocols = ['PR', 'RR']
    # source_methods = ["NOP", "OP_DP", "OP_BP"]
    source_methods = ["NOP", "OP"]
    cost_budgets = COST_BUDGETS

    all_plot_data = []
    results = {f'{sm}-{p}': {'ce_actual': [], 'dr': [], 'ci': []}
               for p in protocols for sm in source_methods}

    for method in source_methods:
        for protocol in protocols:
            for budget in cost_budgets:
                label = f'{method}-{protocol}'
                print(f"\n--- Running: {label}, Budget={budget} ---")
                params = {
                    'length_network': LENGTH_NETWORK_PROTOCOLS_1, 'width_network': WIDTH_NETWORK_PROTOCOLS_1,
                    'num_users': NUM_USERS_PROTOCOLS_1,
                    'p_op': OP_PROTOCOLS_1, 'routing_method': protocol,
                    'source_method': method, 'cost_budget': budget
                }
                summary = run_and_get_metrics(params, user_sets_list)

                results[label]['dr'].append(summary['average_dr'])
                results[label]['ci'].append(summary['ci95_halfwidth'])  # 新增

                row = {
                    'Protocol': protocol, 'Source Method': method, 'Budget': budget,
                    'Deployed_Dicts': summary.get('deployed_dicts', 'N/A'),
                    'user_sets': str(user_sets_list)
                }
                row.update(summary)
                all_plot_data.append(row)

    pd.DataFrame(all_plot_data).to_excel(excel_writer, sheet_name='Fig1_Protocols_vs_Budget', index=False)

    fig, ax = create_dr_plot('Protocols vs. Quantum Source Budget', x_label='Quantum Source Budget')
    x_positions = np.arange(len(cost_budgets), dtype=float)
    plot_grouped_bars(
        ax, results, x_positions, [str(b) for b in cost_budgets],
        group_key_fn=lambda lbl: lbl.split('-', 1)[0]
    )

    # current_ylim = ax.get_ylim()
    # ax.set_ylim(current_ylim[0], current_ylim[1] * 1.05)

    place_legend_inside(ax, loc='upper left')
    fig.subplots_adjust(left=0.15)
    fig.savefig(os.path.join(output_dir, '1_protocols_vs_budget.png'), dpi=600, bbox_inches='tight')
    plt.close(fig)
    print("\n[SUCCESS] Fig.1 saved and data exported.")


# ===================== FIGURE 2 =====================
def plot_mpp_op_var(excel_writer, output_dir, user_sets_list):
    """Fig.2: Proactive Routing — x = p_op, budget fixed"""
    print("\n" + "#" * 60)
    print("###   GENERATING FIG.2: Proactive Routing — x = p_op (Budget fixed)   ###")
    print("#" * 60)

    p_ops = P_OP_LIST_2
    # source_methods = ["NOP", "OP", "OP_BP"]
    source_methods = ["NOP", "OP"]
    protocols = ['PR', 'RR']

    all_plot_data = []
    results = {f'{sm}-{p}': {'ce_actual': [], 'dr': [], 'ci': []}
               for p in protocols for sm in source_methods}

    for method in source_methods:
        for protocol in protocols:
            for p_op in p_ops:
                budget = FIXED_BUDGET
                label = f'{method}-{protocol}'
                print(f"\n--- Running: {label}, Budget={budget} ---")
                params = {
                    'length_network': LENGTH_NETWORK_OP_2, 'width_network': WIDTH_NETWORK_OP_2,
                    'num_users': NUM_USERS_OP_2,
                    'p_op': p_op, 'routing_method': protocol,
                    'source_method': method, 'cost_budget': budget
                }
                summary = run_and_get_metrics(params, user_sets_list)

                results[label]['dr'].append(summary['average_dr'])
                results[label]['ci'].append(summary['ci95_halfwidth'])  # 新增

                row = {
                    'Protocol': protocol,
                    'p_op': p_op, 'Source Method': method, 'Budget(fixed)': budget,
                    'Deployed_Dicts': summary.get('deployed_dicts', 'N/A'),
                    'user_sets': str(user_sets_list)
                }
                row.update(summary)
                all_plot_data.append(row)

    pd.DataFrame(all_plot_data).to_excel(excel_writer, sheet_name='Fig2_p_op_Var', index=False)

    fig, ax = create_dr_plot('Protocols vs Operation Probability', x_label='Operation Probability')

    x_positions = np.arange(len(p_ops), dtype=float)
    plot_grouped_bars(
        ax, results, x_positions, [str(p) for p in p_ops],
        group_key_fn=lambda lbl: lbl.split('-', 1)[0]  # p_op0.6 / p_op0.7 ...
    )

    # plot_grouped_lines(ax, results, p_ops)

    # current_ylim = ax.get_ylim()
    # ax.set_ylim(current_ylim[0], current_ylim[1] * 1.05)

    place_legend_inside(ax)
    fig.subplots_adjust(left=0.15)
    fig.savefig(os.path.join(output_dir, '2_proactive_op_var.png'), dpi=600, bbox_inches='tight')
    plt.close(fig)
    print("\n[SUCCESS] Fig.2 saved and data exported.")


# ===================== FIGURE 3 =====================
def plot_mpp_scalability_network_size_var(excel_writer, output_dir, base_user_sets_lists):
    """Fig.3: Proactive Routing — x = Network Size, budget fixed"""
    print("\n" + "#" * 60)
    print("###   GENERATING FIG.3: Proactive Routing — x = Network Size (Budget fixed)   ###")
    print("#" * 60)

    sizes = NET_SIZES_3
    # source_methods = ["NOP", "OP", "OP_BP"]
    source_methods = ["NOP", "OP"]
    protocols = ['PR', 'RR']

    all_plot_data = []
    results = {f'{sm}-{p}': {'ce_actual': [], 'dr': [], 'ci': []}
               for p in protocols for sm in source_methods}

    for method in source_methods:
        for protocol in protocols:
            for size in sizes:
                user_sets_list = base_user_sets_lists[f'{size[0]}x{size[1]}']
                budget = FIXED_BUDGET
                label = f'{method}-{protocol}'
                print(f"\n--- Running: {label}, Budget={budget} ---")
                params = {
                    'length_network': size[0], 'width_network': size[1], 'num_users': NUM_USERS_NET_SIZE_3,
                    'p_op': OP_NET_SIZE_3, 'routing_method': protocol,
                    'source_method': method, 'cost_budget': budget
                }
                summary = run_and_get_metrics(params, user_sets_list)

                results[label]['dr'].append(summary['average_dr'])
                results[label]['ci'].append(summary['ci95_halfwidth'])  # 新增

                row = {
                    'Protocol': protocol,
                    'Network_Size': f'{size[0]}x{size[1]}', 'Source Method': method, 'Budget(fixed)': budget,
                    'Deployed_Dicts': summary.get('deployed_dicts', 'N/A'),
                    'user_sets': str(user_sets_list)
                }
                row.update(summary)
                all_plot_data.append(row)

    pd.DataFrame(all_plot_data).to_excel(excel_writer, sheet_name='Fig3_NetSize_Var', index=False)

    fig, ax = create_dr_plot('Protocols vs Network Size: DR', x_label='Network Size')
    x_positions = np.arange(len(sizes), dtype=float)
    ticklabels = [f'{a}x{b}' for a, b in sizes]
    plot_grouped_bars(
        ax, results, x_positions, ticklabels,
        group_key_fn=lambda lbl: lbl.split('-', 1)[0]  # '3x3' / '4x4' / '5x5'
    )

    # current_ylim = ax.get_ylim()
    # ax.set_ylim(current_ylim[0], current_ylim[1] * 1.05)

    place_legend_inside(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, '3_proactive_network_size_var.png'))
    plt.close(fig)
    print("\n[SUCCESS] Fig.3 saved and data exported.")


# ===================== FIGURE 4 =====================
def plot_mpp_scalability_users_var(excel_writer, output_dir, base_user_sets_lists):
    """Fig.4: Proactive Routing — x = #Users, budget fixed"""
    print("\n" + "#" * 60)
    print("###   GENERATING FIG.4: Proactive Routing — x = #Users (Budget fixed)   ###")
    print("#" * 60)

    num_users_list = NUM_USERS_LIST_4
    # source_methods = ["NOP", "OP_DP", "OP_BP"]
    source_methods = ["NOP", "OP"]
    protocols = ['PR', 'RR']

    all_plot_data = []
    results = {f'{sm}-{p}': {'ce_actual': [], 'dr': [], 'ci': []}
               for p in protocols for sm in source_methods}

    for method in source_methods:
        for protocol in protocols:
            for num_users in num_users_list:
                user_sets_list = base_user_sets_lists[f'{num_users}_users']
                budget = FIXED_BUDGET
                label = f'{method}-{protocol}'
                print(f"\n--- Running: {label}, Budget={budget} ---")
                params = {
                    'length_network': LENGTH_NETWORK_USER_4, 'width_network': WIDTH_NETWORK_USER_4, 'num_users': num_users,
                    'p_op': OP_USER_4, 'routing_method': protocol,
                    'source_method': method, 'cost_budget': budget
                }
                summary = run_and_get_metrics(params, user_sets_list)

                results[label]['dr'].append(summary['average_dr'])
                results[label]['ci'].append(summary['ci95_halfwidth'])  # 新增

                row = {
                    'Protocol': protocol,
                    'Num_Users': num_users, 'Source Method': method, 'Budget(fixed)': budget,
                    'Deployed_Dicts': summary.get('deployed_dicts', 'N/A'),
                    'user_sets': str(user_sets_list)
                }
                row.update(summary)
                all_plot_data.append(row)

    pd.DataFrame(all_plot_data).to_excel(excel_writer, sheet_name='Fig4_NumUsers_Var', index=False)

    fig, ax = create_dr_plot('Protocols vs Number of Users', x_label='Number of Users', figuresize=(8, 6.5))
    x_positions = np.arange(len(num_users_list), dtype=float)
    plot_grouped_bars(
        ax, results, x_positions, [str(n) for n in num_users_list],
        group_key_fn=lambda lbl: lbl.split('-', 1)[0]  # '3users' / '4users' / '5users'
    )

    # current_ylim = ax.get_ylim()
    # ax.set_ylim(current_ylim[0], current_ylim[1] * 1.05)

    place_legend_inside(ax, loc='upper right', bbox_to_anchor=(0.98, 0.98))
    fig.subplots_adjust(left=0.15)
    fig.savefig(os.path.join(output_dir, '4_proactive_num_users_var.png'), dpi=600, bbox_inches='tight')
    plt.close(fig)
    print("\n[SUCCESS] Fig.4 saved and data exported.")


# ===================== FIGURE 5 =====================
def plot_mpp_decoherence_var(excel_writer, output_dir, user_sets_list):
    """Fig.5: Proactive Routing — x = Decoherence Time, budget fixed"""
    print("\n" + "#" * 60)
    print("###   GENERATING FIG.5: Proactive Routing — x = Decoherence Time (Budget fixed)   ###")
    print("#" * 60)

    dt_list = DT_LIST_5
    # source_methods = ["NOP", "OP_DP", "OP_BP"]
    source_methods = ["NOP", "OP"]
    protocols = ['PR', 'RR']

    all_plot_data = []
    results = {f'{sm}-{p}': {'ce_actual': [], 'dr': [], 'ci': []}
               for p in protocols for sm in source_methods}

    for method in source_methods:
        for protocol in protocols:
            for dt in dt_list:
                budget = FIXED_BUDGET
                label = f'{method}-{protocol}'
                print(f"\n--- Running: {label}, Budget={budget} ---")
                params = {
                    'length_network': LENGTH_NETWORK_DT_5, 'width_network': WIDTH_NETWORK_DT_5,
                    'num_users': NUM_USERS_DT_5,
                    'p_op': OP_DT_5,
                    'routing_method': protocol,
                    'source_method': method,
                    'cost_budget': budget,
                    'decoherence_time': dt
                }
                summary = run_and_get_metrics(params, user_sets_list)

                results[label]['dr'].append(summary['average_dr'])
                results[label]['ci'].append(summary['ci95_halfwidth'])  # 新增

                row = {
                    'Protocol': protocol,
                    'Decoherence_Time': dt, 'Source Method': method, 'Budget(fixed)': budget,
                    'Deployed_Dicts': summary.get('deployed_dicts', 'N/A'),
                    'user_sets': str(user_sets_list)
                }
                row.update(summary)
                all_plot_data.append(row)

    pd.DataFrame(all_plot_data).to_excel(excel_writer, sheet_name='Fig5_Decoherence_Var', index=False)

    fig, ax = create_dr_plot('Protocols vs Decoherence Time', x_label='Decoherence Time')
    x_positions = np.arange(len(dt_list), dtype=float)
    plot_grouped_bars(
        ax, results, x_positions, [str(dt) for dt in dt_list],
        group_key_fn=lambda lbl: lbl.split('-', 1)[0]  # 'dt1' / 'dt2' ...
    )

    # current_ylim = ax.get_ylim()
    # ax.set_ylim(current_ylim[0], current_ylim[1] * 1.05)

    place_legend_inside(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, '5_proactive_decoherence_var.png'))
    plt.close(fig)
    print("\n[SUCCESS] Fig.5 saved and data exported.")


# ===================== MAIN =====================
if __name__ == "__main__":
    timestamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_directory = os.path.join('simulation_plots', timestamp)
    os.makedirs(output_directory, exist_ok=True)
    print(f"Created output directory: {output_directory}")

    excel_filepath = os.path.join(output_directory, f"simulation_results_{timestamp}.xlsx")

    print("Pre-generating user sets for consistency...")
    random.seed(RANDOM_SEED)

    # For Fig.1, Fig.2, Fig.5 (same 5x5, 3 users)
    # 1) 用真实拓扑的节点集合初始化 RequestGenerator
    all_nodes = nodes_from_edge_list(EDGE_LIST)
    rg = RequestGenerator(all_nodes)

    # 2) 同一真实拓扑 + 固定用户数 NUM_USERS_PROTOCOLS_1
    user_sets_1_2_5 = [rg.random_users(k=NUM_USERS_PROTOCOLS_1)
                       for _ in range(NUM_TRIALS)]

    # For Fig.3 (per network size, 3 users)

    # For Fig.4 (per num_users on 3x3)
    base_user_sets_4 = {}
    for n in NUM_USERS_LIST_4:
        base_user_sets_4[f'{n}_users'] = [rg.random_users(k=n)
                                          for _ in range(NUM_TRIALS)]

    print("User set generation complete.")

    with pd.ExcelWriter(excel_filepath, engine='openpyxl') as writer:
        # 占位页，避免异常时没有可见 sheet
        pd.DataFrame([{"status": "initializing"}]).to_excel(writer, sheet_name="README", index=False)

        export_run_parameters(writer)
        #
        plot_protocols_vs_budget(writer, output_directory, user_sets_1_2_5)                  # Fig.1
        plot_mpp_op_var(writer, output_directory, user_sets_1_2_5)                           # Fig.2
        # plot_mpp_scalability_network_size_var(writer, output_directory, base_user_sets_3)    # Fig.3
        plot_mpp_scalability_users_var(writer, output_directory, base_user_sets_4)           # Fig.4
        # plot_mpp_decoherence_var(writer, output_directory, user_sets_1_2_5)                  # Fig.5

    print("\n\n" + "*" * 50)
    print("********** ALL SIMULATIONS COMPLETE   **********")
    print(f"All plot images and data saved in '{output_directory}'.")
    print("*" * 50)
