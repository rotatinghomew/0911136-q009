"""轮班交叉试点的效果分析：主分析遵循原始分配，推断尊重受限随机设计。

设计事实（见 docs/data-contract.md）：

- 随机化单位是 team-block，每个区组只能从 allowed_sequences 整体抽取序列，
  不能按事项独立置换，因此不能把每个电话当作独立样本计算显著性。
- 主分析按 assigned_mode 分组（ITT），不按 actual_mode。
- completed_at 为 null 的事项在观察窗内未完成：按删失处理，既不是零耗时，
  也不能只保留已完成事项。
- 跨班遗留、共用队伍与删失对可比性的影响在 comparability 中量化；
  探索性描述与可归因于试点设计的证据分开，不为推广建议强行选赢家。
"""

from itertools import product
from math import prod
from statistics import median

from .analysis import assignment_profile

HORIZON_HOURS = 6.0
MIN_ITEMS_PER_ARM = 3
ALPHA = 0.05
MAX_ENUMERATION = 200000
MODES = ("A", "B")

ESTIMAND = "ITT：按原始分配（assigned_mode）比较两种叫应方式，不按实际执行方式"
METHOD = "受限随机化精确检验：每个区组仅在允许序列内整体重抽并枚举全部组合，统计量取 B−A，双侧"


def _hours(opened_at, finished_at):
    from datetime import datetime
    return (datetime.fromisoformat(finished_at) - datetime.fromisoformat(opened_at)).total_seconds() / 3600.0


def _prepare_items(tables, cutoff):
    """按 known_at 过滤并联结班次分配；未结事项保留为删失记录，不计为零耗时。"""
    assignments = {row["shift_id"]: row for row in tables["assignments"]}
    items = []
    skipped = 0
    for row in tables["items"]:
        if row["known_at"] > cutoff:
            skipped += 1
            continue
        shift = assignments[row["shift_id"]]
        completed = row["completed_at"] is not None
        items.append({
            "item_id": row["item_id"],
            "shift_id": row["shift_id"],
            "block_id": shift["block_id"],
            "team_id": shift["team_id"],
            "assigned_mode": shift["assigned_mode"],
            "actual_mode": row["actual_mode"],
            "event": completed,
            "time": _hours(row["opened_at"], row["completed_at"] if completed else cutoff),
            "missed": row["missed"],
            "open_at_shift_end": (not completed) or row["completed_at"] > shift["end"],
        })
    return items, skipped


def _check_blocks(tables, items):
    """逐区组核验设计与样本量；违背设计或样本不足的区组降级，不进入主分析。"""
    blocks = {row["block_id"]: row for row in tables["blocks"]}
    shifts_by_block = {}
    for row in tables["assignments"]:
        shifts_by_block.setdefault(row["block_id"], []).append(row)
    arm_items = {block_id: {mode: 0 for mode in MODES} for block_id in shifts_by_block}
    for item in items:
        arm_items[item["block_id"]][item["assigned_mode"]] += 1
    included, downgraded, details = [], [], {}
    for block_id in sorted(shifts_by_block):
        rows = sorted(shifts_by_block[block_id], key=lambda row: row["shift_id"])
        observed = [row["assigned_mode"] for row in rows]
        allowed = [list(sequence) for sequence in blocks[block_id]["allowed_sequences"]]
        reasons = []
        if observed not in allowed:
            reasons.append("分配序列不在允许集合，无法确认该区组的随机化分布")
        for mode in MODES:
            if mode not in observed:
                reasons.append(f"区组内缺少 {mode} 方式班次")
            elif arm_items[block_id][mode] < MIN_ITEMS_PER_ARM:
                reasons.append(f"区组内 {mode} 方式事项数低于下限 {MIN_ITEMS_PER_ARM}")
        details[block_id] = {
            "shift_ids": [row["shift_id"] for row in rows],
            "observed": observed,
            "allowed": allowed,
        }
        if reasons:
            downgraded.append({"block_id": block_id, "reasons": reasons})
        else:
            included.append(block_id)
    return included, downgraded, details


def _km_rmst(samples, horizon):
    """Kaplan-Meier 受限平均完成时长。samples 为 (时间, 是否完成) 并按时间升序。

    删失记录保留在风险集中：既不计为零耗时，也不被丢弃。
    """
    survival = 1.0
    rmst = 0.0
    last = 0.0
    total = len(samples)
    index = 0
    while index < total:
        t = samples[index][0]
        if t > horizon:
            break
        end = index
        events = 0
        while end < total and samples[end][0] == t:
            if samples[end][1]:
                events += 1
            end += 1
        if events:
            rmst += survival * (t - last)
            survival *= 1.0 - events / (total - index)
            last = t
        index = end
    rmst += survival * (horizon - last)
    return rmst, survival


def _duration_statistic(duration_by_shift):
    def statistic(mode_map):
        samples = {mode: [] for mode in MODES}
        for shift_id, mode in mode_map.items():
            samples[mode].extend(duration_by_shift[shift_id])
        rmst = {}
        for mode in MODES:
            rmst[mode], _ = _km_rmst(sorted(samples[mode]), HORIZON_HOURS)
        return rmst["B"] - rmst["A"]
    return statistic


def _missed_statistic(missed_by_shift):
    def statistic(mode_map):
        known = {mode: [] for mode in MODES}
        for shift_id, mode in mode_map.items():
            known[mode].extend(value for value in missed_by_shift[shift_id] if value is not None)
        if not known["A"] or not known["B"]:
            return None
        return sum(known["B"]) / len(known["B"]) - sum(known["A"]) / len(known["A"])
    return statistic


def _assignment_enumeration(included, details):
    for combo in product(*(details[block]["allowed"] for block in included)):
        mode_map = {}
        for block_id, sequence in zip(included, combo):
            for shift_id, mode in zip(details[block_id]["shift_ids"], sequence):
                mode_map[shift_id] = mode
        yield mode_map


def _quantiles(values):
    ordered = sorted(values)
    last = len(ordered) - 1

    def at(p):
        return ordered[min(last, max(0, int(p * last + 0.5)))]

    return {"2.5%": at(0.025), "50%": at(0.5), "97.5%": at(0.975)}


def _randomization_test(statistic, observed, included, details):
    """精确枚举受限随机化的全部分配；p 值为零分布中不弱于观测值的比例。"""
    total = prod(len(details[block]["allowed"]) for block in included)
    if total > MAX_ENUMERATION:
        raise ValueError("受限分配组合数超出精确枚举上限，需改用固定种子的蒙特卡洛近似")
    result = {"permutations": total}
    if observed is None:
        result["p_two_sided"] = None
        result["note"] = "观测统计量无法定义（某方式下无已知状态事项），未计算 p 值"
        return result
    extreme = 0
    undefined = 0
    values = []
    for mode_map in _assignment_enumeration(included, details):
        value = statistic(mode_map)
        if value is None:
            undefined += 1
            continue
        values.append(value)
        if abs(value) >= abs(observed) - 1e-9:
            extreme += 1
    result["p_two_sided"] = extreme / total
    result["null_quantiles"] = _quantiles(values)
    if undefined:
        result["undefined_permutations"] = undefined
    return result


def _arm_duration_stats(items, key):
    stats = {}
    for mode in sorted({item[key] for item in items}):
        selected = [item for item in items if item[key] == mode]
        samples = sorted((item["time"], item["event"]) for item in selected)
        rmst, survival = _km_rmst(samples, HORIZON_HOURS)
        durations = [item["time"] for item in selected if item["event"]]
        stats[mode] = {
            "items": len(selected),
            "completed": len(durations),
            "censored": len(selected) - len(durations),
            "rmst_hours": rmst,
            "completion_within_horizon": 1.0 - survival,
            "median_completed_hours": median(durations) if durations else None,
        }
    return stats


def _arm_missed_stats(items, key):
    stats = {}
    for mode in sorted({item[key] for item in items}):
        selected = [item for item in items if item[key] == mode]
        known = [item["missed"] for item in selected if item["missed"] is not None]
        stats[mode] = {
            "items": len(selected),
            "status_known": len(known),
            "status_unknown": len(selected) - len(known),
            "missed": sum(known),
            "risk": sum(known) / len(known) if known else None,
        }
    return stats


def _confirmatory(items, included, details):
    if not included:
        return {
            "available": False,
            "estimand": ESTIMAND,
            "reason": "没有符合设计要求且样本足够的区组，无法进行设计内推断",
        }
    in_scope = [item for item in items if item["block_id"] in set(included)]
    duration_by_shift = {}
    missed_by_shift = {}
    for item in in_scope:
        duration_by_shift.setdefault(item["shift_id"], []).append((item["time"], item["event"]))
        missed_by_shift.setdefault(item["shift_id"], []).append(item["missed"])
    observed_map = {}
    for block_id in included:
        for shift_id, mode in zip(details[block_id]["shift_ids"], details[block_id]["observed"]):
            observed_map[shift_id] = mode
    duration_stat = _duration_statistic(duration_by_shift)
    missed_stat = _missed_statistic(missed_by_shift)
    duration_observed = duration_stat(observed_map)
    missed_observed = missed_stat(observed_map)
    return {
        "available": True,
        "estimand": ESTIMAND,
        "method": METHOD,
        "duration": {
            "metric": f"完成时长 RMST 差（B−A，小时，观察界 {HORIZON_HOURS}h；正值表示 B 更慢）",
            "estimate": duration_observed,
            **_randomization_test(duration_stat, duration_observed, included, details),
            "arms": _arm_duration_stats(in_scope, "assigned_mode"),
        },
        "missed": {
            "metric": "漏接风险差（B−A，已知状态事项中的漏接比例；正值表示 B 漏接更多）",
            "estimate": missed_observed,
            **_randomization_test(missed_stat, missed_observed, included, details),
            "arms": _arm_missed_stats(in_scope, "assigned_mode"),
        },
    }


def _carryover(tables, items):
    """量化跨班遗留：班次末仍未结的事项会占用下一班（可能相反方式）的资源。"""
    shifts_by_team = {}
    for row in tables["assignments"]:
        shifts_by_team.setdefault(row["team_id"], []).append(row)
    items_by_shift = {}
    for item in items:
        items_by_shift.setdefault(item["shift_id"], []).append(item)
    profile = {
        "items_open_at_shift_end": 0,
        "carried_into_opposite_mode": 0,
        "carried_into_same_mode": 0,
        "carried_across_block_boundary": 0,
        "still_open_at_cutoff": sum(1 for item in items if not item["event"]),
    }
    for team_shifts in shifts_by_team.values():
        ordered = sorted(team_shifts, key=lambda row: row["start"])
        for index, shift in enumerate(ordered):
            carried = [item for item in items_by_shift.get(shift["shift_id"], []) if item["open_at_shift_end"]]
            if not carried or index + 1 >= len(ordered):
                profile["items_open_at_shift_end"] += len(carried)
                continue
            following = ordered[index + 1]
            profile["items_open_at_shift_end"] += len(carried)
            if following["block_id"] != shift["block_id"]:
                profile["carried_across_block_boundary"] += len(carried)
            if following["assigned_mode"] != shift["assigned_mode"]:
                profile["carried_into_opposite_mode"] += len(carried)
            else:
                profile["carried_into_same_mode"] += len(carried)
    profile["effect"] = (
        "上一班未结事项占用下一班资源；流入相反方式班次的遗留会把上一班方式的影响带进下一班，"
        "稀释 ITT 组间差异（偏向无差异），并使班次层面不再独立可比。"
    )
    return profile


def _shared_team(tables):
    teams = {row["team_id"]: row for row in tables["teams"]}
    blocks_by_team = {team_id: set() for team_id in teams}
    for row in tables["assignments"]:
        blocks_by_team.setdefault(row["team_id"], set()).add(row["block_id"])
    return {
        "teams": {
            team_id: {
                "towns": teams[team_id]["town_ids"],
                "blocks": sorted(blocks_by_team.get(team_id, set())),
            }
            for team_id in sorted(teams)
        },
        "multi_town_teams": sorted(team_id for team_id in teams if len(teams[team_id]["town_ids"]) > 1),
        "effect": (
            "多乡镇共用同一队伍：team-block 内部的相互占用已被随机化单位覆盖，"
            "但同一队伍跨区组的遗留与跨乡镇的资源竞争未被设计隔离，区组间独立性只是近似成立。"
        ),
    }


def _comparability(tables, items, cutoff):
    censored = [item for item in items if not item["event"]]
    return {
        "carryover": _carryover(tables, items),
        "shared_team": _shared_team(tables),
        "censoring": {
            "censored_items": len(censored),
            "censored_share": len(censored) / len(items) if items else 0.0,
            "censor_at": cutoff,
            "missed_status_unknown_for_censored": sum(1 for item in censored if item["missed"] is None),
            "effect": (
                "观察窗内未结事项按删失处理：计为零耗时会严重低估完成时长，只保留完成事项会丢弃这部分样本"
                "并高估完成水平；漏接状态仅在已完成事项上可知，已知状态口径的漏接风险存在选择偏差。"
            ),
        },
    }


def _exploratory(items, included):
    in_scope = [item for item in items if item["block_id"] in set(included)]
    per_block = []
    for block_id in included:
        block_items = [item for item in in_scope if item["block_id"] == block_id]
        duration = _arm_duration_stats(block_items, "assigned_mode")
        missed = _arm_missed_stats(block_items, "assigned_mode")
        per_block.append({
            "block_id": block_id,
            "rmst_diff_hours": (duration["B"]["rmst_hours"] - duration["A"]["rmst_hours"])
                               if {"A", "B"} <= set(duration) else None,
            "missed_risk_diff": (missed["B"]["risk"] - missed["A"]["risk"])
                                if {"A", "B"} <= set(missed) and missed["A"]["risk"] is not None
                                and missed["B"]["risk"] is not None else None,
        })
    completed_means = {}
    completed_counts = {}
    for mode in MODES:
        durations = [item["time"] for item in in_scope if item["assigned_mode"] == mode and item["event"]]
        completed_means[mode] = sum(durations) / len(durations) if durations else None
        completed_counts[mode] = len(durations)
    return {
        "warning": "以下为探索性描述，不遵循分配设计，不能作为试点效果证据",
        "as_treated": {
            "note": "按 actual_mode 分组，受执行选择影响，不能归因于分配",
            "duration": _arm_duration_stats(in_scope, "actual_mode"),
            "missed": _arm_missed_stats(in_scope, "actual_mode"),
        },
        "completed_only_naive": {
            "note": "仅已完成事项的均值，丢弃删失事项，系统性低估完成时长，仅用于展示偏差方向",
            "means": completed_means,
            "completed_counts": completed_counts,
        },
        "per_block": per_block,
    }


def _conclusion(confirmatory):
    if not confirmatory["available"]:
        return {
            "attributable_difference_found": False,
            "text": "符合设计要求的区组不足，无法形成设计内推断；现阶段不应据此推广任一叫应方式。",
        }
    findings = []
    duration = confirmatory["duration"]
    if duration["p_two_sided"] is not None and duration["p_two_sided"] < ALPHA:
        direction = "B 慢于 A" if duration["estimate"] > 0 else "B 快于 A"
        findings.append(f"处置完成时长（{direction}，p={duration['p_two_sided']:.4f}）")
    missed = confirmatory["missed"]
    if missed["p_two_sided"] is not None and missed["p_two_sided"] < ALPHA:
        direction = "B 高于 A" if missed["estimate"] > 0 else "B 低于 A"
        findings.append(f"漏接风险（{direction}，p={missed['p_two_sided']:.4f}）")
    if findings:
        text = ("；".join(findings) +
                " 显示可由分配设计归因的差异；但跨班遗留与共用队伍会稀释组间对比，"
                "推广前必须结合 comparability 说明复核。")
    else:
        text = ("两种叫应方式在处置完成时长与漏接风险上均未达到设计内显著性标准；"
                "不存在可宣布的赢家，不建议据此推广任一方式。")
    return {"attributable_difference_found": bool(findings), "text": text}


def pilot_effect(tables, metadata):
    """试点效果分析入口：主分析 ITT + 受限随机化精确检验，探索性结果分开展示。"""
    if "cutoff" not in metadata:
        raise ValueError("缺少分析截点 metadata.cutoff")
    cutoff = metadata["cutoff"]
    assignment_profile(tables)
    items, skipped = _prepare_items(tables, cutoff)
    included, downgraded, details = _check_blocks(tables, items)
    confirmatory = _confirmatory(items, included, details)
    return {
        "kind": "pilot_effect_analysis",
        "design": {
            "randomization_unit": "team-block",
            "analysis_cutoff": cutoff,
            "horizon_hours": HORIZON_HOURS,
            "min_items_per_arm": MIN_ITEMS_PER_ARM,
            "alpha": ALPHA,
            "included_blocks": included,
            "downgraded_blocks": downgraded,
            "records_skipped_after_cutoff": skipped,
            "notes": [
                "随机化单位是 team-block；本数据共 8 个区组，192 个电话不是独立样本，"
                "按电话逐个计算显著性违反设计并夸大证据。",
                "精确检验枚举每个区组的允许序列，最小可达双侧 p 值约为 1/2^区组数；"
                "区组被降级后设计分辨率进一步下降。",
                "完成时长与漏接风险为两个预设结局，各自在 α=0.05 检验，未做多重比较校正。",
                "主分析按原始分配（ITT）；actual_mode 与分配不一致的事项仍归入原分配方式。",
            ],
        },
        "confirmatory": confirmatory,
        "comparability": _comparability(tables, items, cutoff),
        "exploratory": _exploratory(items, included),
        "conclusion": _conclusion(confirmatory),
        "scope": (
            "主分析为遵循受限随机设计的 ITT 精确推断；exploratory 部分不遵循分配设计，"
            "仅供形成假设；跨班遗留、共用队伍与删失限制可比性，详见 comparability。"
        ),
    }
