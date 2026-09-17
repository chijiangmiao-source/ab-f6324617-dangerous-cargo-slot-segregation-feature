"""寻位纯函数单元测试：曼哈顿壳层展开与安全箱位选择。"""

import random

from app.models import Container
from app.rules import _shell_positions, recommend_slot


def C(row: int, col: int, tier: int, category: str) -> Container:
    return Container(row=row, col=col, tier=tier, category=category)  # type: ignore[arg-type]


def manhattan(p, q):
    return abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2])


def shell_list(bounds, expected, distance, occupied=()):
    return list(_shell_positions(bounds, expected, distance, set(occupied)))


# ---------- 壳层枚举本身 ----------

def test_shell_zero_is_expected_point_itself():
    assert shell_list((10, 10, 5), (5, 5, 3), 0) == [(5, 5, 3)]


def test_shell_covers_every_in_hold_point_at_exact_distance():
    bounds, expected = (7, 6, 4), (4, 3, 2)
    for distance in range(0, 9):
        got = shell_list(bounds, expected, distance)
        # 无占用：穷举全舱对照，每个点恰好出现在它所属的壳层上。
        want = [
            (r, c, t)
            for r in range(1, bounds[0] + 1)
            for c in range(1, bounds[1] + 1)
            for t in range(1, bounds[2] + 1)
            if manhattan((r, c, t), expected) == distance
        ]
        assert got == want


def test_shell_emits_in_lexicographic_order():
    got = shell_list((8, 8, 8), (4, 4, 4), 3)
    assert got == sorted(got)


def test_shell_excludes_occupied_positions():
    expected = (4, 4, 4)
    occupied = {(4, 4, 5), (4, 4, 3), (4, 3, 4), (4, 5, 4)}
    got = shell_list((8, 8, 8), expected, 1, occupied)
    assert set(got).isdisjoint(occupied)
    assert got == [(3, 4, 4), (5, 4, 4)]  # 仅剩排方向两点，仍按字典序


def test_shell_near_corner_only_emits_in_hold_points():
    # 期望点贴角 (1,1,1)：壳层只能向舱内方向长。
    got = shell_list((3, 3, 3), (1, 1, 1), 1)
    assert got == [(1, 1, 2), (1, 2, 1), (2, 1, 1)]


def test_shells_partition_whole_hold_without_gap_or_duplicate():
    # 所有壳层拼接起来恰好等于全舱格点集合，不重不漏。
    bounds, expected = (5, 4, 3), (2, 4, 1)
    max_distance = (
        max(expected[0] - 1, bounds[0] - expected[0])
        + max(expected[1] - 1, bounds[1] - expected[1])
        + max(expected[2] - 1, bounds[2] - expected[2])
    )
    seen: list[tuple[int, int, int]] = []
    for distance in range(max_distance + 1):
        seen.extend(shell_list(bounds, expected, distance))
    assert len(seen) == len(set(seen)) == bounds[0] * bounds[1] * bounds[2]
    assert min(seen) == (1, 1, 1)
    assert max(seen) == bounds


def test_shell_beyond_farthest_in_hold_layer_is_empty():
    # 最远壳层之外产出为空，搜索会正常终止而非无限外扩。
    bounds, expected = (3, 3, 3), (2, 2, 2)
    assert shell_list(bounds, expected, 4) == []


# ---------- recommend_slot ----------

def test_empty_hold_recommends_expected_point_with_distance_zero():
    result = recommend_slot([], "A", (5, 5, 2), (10, 10, 5))
    assert result.position == (5, 5, 2)
    assert result.search_distance == 0


def test_expected_point_occupied_falls_back_to_lexicographic_neighbor():
    # 待装 A 与现存 A 同类只要求距离 1；期望点被占，距离 1 的壳层上
    # (1,1,2)/(1,2,1)/(2,1,1) 并列安全，取字典序首位。
    result = recommend_slot([C(1, 1, 1, "A")], "A", (1, 1, 1), (5, 5, 3))
    assert result.position == (1, 1, 2)
    assert result.search_distance == 1


def test_tie_at_nearest_layer_picks_lexicographic_first():
    # 期望点 (3,3,2) 被占，距离 1 的壳层上 6 个邻居全部并列安全。
    result = recommend_slot([C(3, 3, 2, "A")], "D", (3, 3, 2), (6, 6, 4))
    assert result.position == (2, 3, 2)
    assert result.search_distance == 1


def test_result_independent_of_existing_container_input_order():
    boxes = [
        C(1, 1, 1, "B"), C(1, 6, 1, "C"),
        C(6, 1, 3, "D"), C(6, 6, 3, "A"),
        C(3, 3, 1, "C"), C(4, 4, 3, "B"),
    ]
    expected = (3, 4, 2)
    reference = recommend_slot(boxes, "A", expected, (7, 7, 4))
    assert reference.position is not None

    rng = random.Random(20260917)
    for _ in range(20):
        shuffled = boxes[:]
        rng.shuffle(shuffled)
        assert recommend_slot(shuffled, "A", expected, (7, 7, 4)) == reference


def test_special_pair_requirements_enforced_against_all_existing_boxes():
    # 待装 A：对现存 B 至少 3、对现存 C 至少 2。
    # 期望点 (4,4,2) 被 B 占据：距离 1、2 的壳层对 B 都不够 3；
    # 距离 3 的字典序首位 (1,4,2) 又被 C 占据，下一项 (2,3,2)
    # 距 B 恰为 3、距 C 恰为 2，同时满足两条规则。
    existing = [C(4, 4, 2, "B"), C(1, 4, 2, "C")]
    result = recommend_slot(existing, "A", (4, 4, 2), (8, 8, 5))
    assert result.position == (2, 3, 2)
    assert result.search_distance == 3
    assert manhattan(result.position, (4, 4, 2)) == 3
    assert manhattan(result.position, (1, 4, 2)) == 2


def test_distance_equal_to_requirement_is_safe():
    # 一维舱（列、层固定为 1）：待装 D 与现存 B 要求 2。
    # 期望点 (3,1,1) 被 B 占据；距离 1 的 (2,1,1)/(4,1,1) 都低一格，
    # 距离 2 壳层上 (1,1,1) 与 (5,1,1) 恰好等于要求 -> 取字典序首位。
    result = recommend_slot([C(3, 1, 1, "B")], "D", (3, 1, 1), (10, 1, 1))
    assert result.position == (1, 1, 1)
    assert result.search_distance == 2


def test_single_cell_hold_occupied_has_no_safe_position():
    # 1x1x1 舱中唯一位置被占 -> 无安全位置。
    result = recommend_slot([C(1, 1, 1, "A")], "B", (1, 1, 1), (1, 1, 1))
    assert result.position is None
    assert result.search_distance is None


def test_completely_full_hold_has_no_safe_slot():
    # 2x1x1 舱两个位置都有箱，任何类别都无处可放。
    existing = [C(1, 1, 1, "A"), C(2, 1, 1, "A")]
    result = recommend_slot(existing, "B", (1, 1, 1), (2, 1, 1))
    assert result.position is None
    assert result.search_distance is None


def test_no_safe_slot_when_isolation_rules_block_every_free_cell():
    # 舱未满但每个空位都违反隔离：待装 A 对 B 要求 3，
    # 3x1x1 舱中 B 居中 (2,1,1)，两端空位距离都只有 1。
    existing = [C(2, 1, 1, "B")]
    result = recommend_slot(existing, "A", (2, 1, 1), (3, 1, 1))
    assert result.position is None
    assert result.search_distance is None


def test_large_sparse_hold_is_fast_without_grid_materialization():
    # 万亿格舱段、仅一个远处现存箱：搜索只触及期望点本身，
    # 若物化完整网格不可能在限时内完成。
    bounds = (1_000_000, 1_000_000, 1_000_000)
    result = recommend_slot([C(1, 1, 1, "B")], "A", (500_000, 500_000, 500_000), bounds)
    assert result.position == (500_000, 500_000, 500_000)
    assert result.search_distance == 0


def test_only_relationship_with_existing_boxes_is_judged():
    # 现存箱彼此冲突（A-B 距离 1 < 3）不影响寻位：
    # 只判断待装箱与各现存箱的隔离关系。
    existing = [C(1, 1, 1, "A"), C(1, 1, 2, "B")]
    result = recommend_slot(existing, "C", (5, 5, 2), (6, 6, 3))
    assert result.position == (5, 5, 2)
    assert result.search_distance == 0
