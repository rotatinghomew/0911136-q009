"""核验班次分配设计并整理完整与未结事项。"""
from collections import Counter, defaultdict


def assignment_profile(tables):
    blocks = {row["block_id"]: row for row in tables["blocks"]}
    grouped = defaultdict(list)
    assignments = {row["shift_id"]: row for row in tables["assignments"]}
    for row in tables["assignments"]:
        grouped[row["block_id"]].append(row)
    violations = []
    for block_id, rows in grouped.items():
        if block_id not in blocks:
            raise ValueError("班次没有随机分配区组")
        sequence = [row["assigned_mode"] for row in sorted(rows, key=lambda row: row["shift_id"])]
        if sequence not in blocks[block_id]["allowed_sequences"]:
            violations.append(block_id)
    counts = Counter()
    for row in tables["items"]:
        if row["shift_id"] not in assignments:
            raise ValueError("事项引用未知班次")
        counts["unresolved" if row["completed_at"] is None else "completed"] += 1
    return {"design_violations": violations, "item_counts": dict(sorted(counts.items()))}


def summarize(tables):
    return {**assignment_profile(tables), "scope": "分配与结案状态摘要，不进行试点效果推断"}
