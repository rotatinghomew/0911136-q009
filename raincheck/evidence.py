"""保留观测来源与复核状态。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    source: str
    value_mm: float | None
    reviewed: bool


def comparable(first: Evidence, second: Evidence) -> bool:
    return first.value_mm is not None and second.value_mm is not None
