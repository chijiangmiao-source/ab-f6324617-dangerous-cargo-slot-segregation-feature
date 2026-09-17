"""隔离规则、裁决逻辑与箱位推荐（纯函数，不依赖 Web 层）。"""

from dataclasses import dataclass
from typing import Iterator, Sequence

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


@dataclass(frozen=True)
class Recommendation:
    """推荐结果：安全箱位坐标，及其与期望坐标的曼哈顿距离（所在壳层）。"""

    row: int
    col: int
    tier: int
    distance: int


def _in_bounds_shell(
    desired: Position,
    max_row: int,
    max_col: int,
    max_tier: int,
    distance: int,
) -> Iterator[Position]:
    """生成与期望点曼哈顿距离恰为 distance 的全部舱内坐标。

    三个轴的偏移先按舱段边界裁剪再组合，因此只产生舱内壳层坐标，
    不为大尺寸稀疏舱段物化完整网格。
    """
    want_row, want_col, want_tier = desired
    row_lo = max(-distance, 1 - want_row)
    row_hi = min(distance, max_row - want_row)
    for d_row in range(row_lo, row_hi + 1):
        remaining = distance - abs(d_row)
        col_lo = max(-remaining, 1 - want_col)
        col_hi = min(remaining, max_col - want_col)
        for d_col in range(col_lo, col_hi + 1):
            d_tier = remaining - abs(d_col)
            tier_up = want_tier + d_tier
            if 1 <= tier_up <= max_tier:
                yield (want_row + d_row, want_col + d_col, tier_up)
            if d_tier:
                tier_down = want_tier - d_tier
                if 1 <= tier_down <= max_tier:
                    yield (want_row + d_row, want_col + d_col, tier_down)


def recommend_slot(
    max_row: int,
    max_col: int,
    max_tier: int,
    containers: Sequence[Container],
    category: str,
    desired: Position,
) -> Recommendation | None:
    """为待装箱搜索离期望坐标最近的安全箱位；全舱无可用位置返回 None。

    从期望点按曼哈顿距离逐层展开：候选必须未被占用，且与全部现存箱的
    实际距离都达到要求距离（沿用固定规则表）；同层候选取 (排, 列, 层)
    字典序最小者，因此结果与现存箱的输入顺序无关。搜索只读，
    不改变已有箱位。
    """
    occupied = {_position(container) for container in containers}

    def is_safe(position: Position) -> bool:
        row, col, tier = position
        for container in containers:
            actual = (
                abs(row - container.row)
                + abs(col - container.col)
                + abs(tier - container.tier)
            )
            if actual < required_distance(category, container.category):
                return False
        return True

    want_row, want_col, want_tier = desired
    # 舱内任意点与期望点的最大曼哈顿距离：各轴取到较远端，超出即无候选。
    farthest = (
        max(want_row - 1, max_row - want_row)
        + max(want_col - 1, max_col - want_col)
        + max(want_tier - 1, max_tier - want_tier)
    )

    for distance in range(farthest + 1):
        best: Position | None = None
        for position in _in_bounds_shell(
            desired, max_row, max_col, max_tier, distance
        ):
            if position in occupied or not is_safe(position):
                continue
            if best is None or position < best:
                best = position
        if best is not None:
            return Recommendation(best[0], best[1], best[2], distance)
    return None
