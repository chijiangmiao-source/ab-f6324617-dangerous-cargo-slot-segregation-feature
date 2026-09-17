"""隔离规则与裁决逻辑（纯函数，不依赖 Web 层）。"""

from collections.abc import Iterator
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


@dataclass(frozen=True)
class SlotRecommendation:
    """寻位结果：无可用位置时 position/search_distance 均为 None。"""

    position: Position | None
    search_distance: int | None


def _shell_positions(
    bounds: tuple[int, int, int],
    expected: Position,
    distance: int,
    occupied: set[Position],
) -> Iterator[Position]:
    """逐个产出与期望点曼哈顿距离恰为 distance 的舱内、未占用坐标。

    按 (排, 列, 层) 字典序升序产出，因此同壳层无需再排序；
    只枚举该壳层上的点（壳层规模 O(d^2)），不物化整个舱段网格，
    大尺寸稀疏舱段同样廉价。
    """
    max_row, max_col, max_tier = bounds
    exp_row, exp_col, exp_tier = expected

    row_lo = max(1, exp_row - distance)
    row_hi = min(max_row, exp_row + distance)
    for row in range(row_lo, row_hi + 1):
        # 固定排后，列、层还需分摊的绝对差之和。
        remain = distance - abs(row - exp_row)
        col_lo = max(1, exp_col - remain)
        col_hi = min(max_col, exp_col + remain)
        for col in range(col_lo, col_hi + 1):
            delta_tier = remain - abs(col - exp_col)
            # 层差固定为 ±delta_tier（为 0 时只有一个点），天然按升序。
            tiers = (
                (exp_tier - delta_tier, exp_tier + delta_tier)
                if delta_tier
                else (exp_tier,)
            )
            for tier in tiers:
                position = (row, col, tier)
                if 1 <= tier <= max_tier and position not in occupied:
                    yield position


def _is_safe_for(
    position: Position,
    cargo_category: str,
    containers: Sequence[Container],
) -> bool:
    """待装箱在该坐标是否对全部现存箱达到最小隔离距离。

    只判断待装箱与各现存箱之间的关系，不复查现存箱彼此之间的隔离。
    距离恰好等于要求即安全（与裁决口径一致：低一格才不行）。
    """
    row, col, tier = position
    for other in containers:
        actual = (
            abs(row - other.row)
            + abs(col - other.col)
            + abs(tier - other.tier)
        )
        if actual < required_distance(cargo_category, other.category):
            return False
    return True


def recommend_slot(
    containers: Sequence[Container],
    cargo_category: str,
    expected: Position,
    bounds: tuple[int, int, int],
) -> SlotRecommendation:
    """从期望点按曼哈顿距离逐层向外寻找首个安全箱位。

    * 第 0 层即期望点本身；每层只生成舱内坐标并跳过已占用位置；
    * 同层坐标按 (排, 列, 层) 字典序依次检验，取首个对全部现存箱
      都满足最小距离者——结果只取决于舱段状态，与现存箱输入顺序无关；
    * 不物化完整网格：逐壳层枚举，枚举到的点才做安全判定；
    * 直到最远的舱内壳层（期望点到舱角的最大曼哈顿距离）仍无候选时，
      返回无可用位置。
    """
    max_row, max_col, max_tier = bounds
    occupied = {_position(container) for container in containers}

    # 期望点到舱段任一格的最大可能曼哈顿距离：再往外没有舱内坐标。
    max_distance = (
        max(expected[0] - 1, max_row - expected[0])
        + max(expected[1] - 1, max_col - expected[1])
        + max(expected[2] - 1, max_tier - expected[2])
    )

    for search_distance in range(max_distance + 1):
        for position in _shell_positions(
            bounds, expected, search_distance, occupied
        ):
            if _is_safe_for(position, cargo_category, containers):
                return SlotRecommendation(position, search_distance)

    return SlotRecommendation(None, None)
