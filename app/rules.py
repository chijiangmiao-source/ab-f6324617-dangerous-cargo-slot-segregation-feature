"""隔离规则与裁决逻辑（纯函数，不依赖 Web 层）。"""

from dataclasses import dataclass
from typing import Sequence

from .models import Container

# 固定规则表（键为无序类别对，对称适用）：
#   A-B 至少 3，A-C 至少 2，B-D 至少 2；
#   其余任意组合（含同类，如 A-A）一律至少 1。
_PAIR_REQUIREMENTS: dict[frozenset[str], int] = {
    frozenset({"A", "B"}): 3,
    frozenset({"A", "C"}): 2,
    frozenset({"B", "D"}): 2,
}
DEFAULT_REQUIREMENT = 1

Position = tuple[int, int, int]


def required_distance(category_a: str, category_b: str) -> int:
    """返回两类危险品箱之间要求的最小曼哈顿距离。"""
    return _PAIR_REQUIREMENTS.get(frozenset({category_a, category_b}), DEFAULT_REQUIREMENT)


def manhattan_distance(a: Container, b: Container) -> int:
    """排差、列差、层差三者绝对值之和（非欧氏距离，不跳过中间空位）。"""
    return (
        abs(a.row - b.row)
        + abs(a.col - b.col)
        + abs(a.tier - b.tier)
    )


@dataclass(frozen=True)
class Conflict:
    smaller: Container
    larger: Container
    actual_distance: int
    required_distance: int


@dataclass(frozen=True)
class AdjudicationResult:
    pairs_compared: int
    first_conflict: Conflict | None


def _position(container: Container) -> Position:
    return (container.row, container.col, container.tier)


def adjudicate(containers: Sequence[Container]) -> AdjudicationResult:
    """比较全部箱位对，返回排序后的首个冲突；无冲突返回比较对数。

    冲突判定：实际距离 < 要求距离（恰好相等为合规）。
    排序键：先按一对箱中较小箱位的 (排, 列, 层)，再按较大箱位的
    (排, 列, 层)；类别不参与排序。因此首个冲突与输入顺序无关，唯一确定。
    """
    first: tuple[tuple[Position, Position], Conflict] | None = None
    pairs_compared = 0

    for i in range(len(containers)):
        for j in range(i + 1, len(containers)):
            left, right = containers[i], containers[j]
            pairs_compared += 1

            actual = manhattan_distance(left, right)
            required = required_distance(left.category, right.category)
            if actual >= required:
                continue

            # 坐标唯一，故同一对内的大小次序无并列。
            if _position(left) > _position(right):
                left, right = right, left
            key = (_position(left), _position(right))
            conflict = Conflict(left, right, actual, required)

            if first is None or key < first[0]:
                first = (key, conflict)

    return AdjudicationResult(
        pairs_compared=pairs_compared,
        first_conflict=None if first is None else first[1],
    )
