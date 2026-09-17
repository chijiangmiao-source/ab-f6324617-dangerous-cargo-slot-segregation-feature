"""规则引擎单元测试。"""

import random

import pytest

from app.models import Container
from app.rules import adjudicate, manhattan_distance, required_distance


def C(row: int, col: int, tier: int, category: str) -> Container:
    return Container(row=row, col=col, tier=tier, category=category)  # type: ignore[arg-type]


# 无序类别对 -> 要求距离；其余（含同类）为 1。
SPECIAL_PAIRS = {
    ("A", "B"): 3,
    ("A", "C"): 2,
    ("B", "D"): 2,
}


def all_unordered_pairs():
    cats = ["A", "B", "C", "D"]
    for i, a in enumerate(cats):
        for b in cats[i:]:
            yield a, b


@pytest.mark.parametrize("a,b", list(all_unordered_pairs()))
def test_requirement_matrix_is_complete(a, b):
    expected = SPECIAL_PAIRS.get((a, b), SPECIAL_PAIRS.get((b, a), 1))
    assert required_distance(a, b) == expected
    # 对称
    assert required_distance(b, a) == expected


def test_manhattan_distance_uses_all_three_axes():
    assert manhattan_distance(C(1, 1, 1, "A"), C(2, 2, 2, "B")) == 3
    assert manhattan_distance(C(5, 3, 4, "A"), C(1, 6, 2, "B")) == 4 + 3 + 2
    assert manhattan_distance(C(2, 2, 2, "A"), C(2, 2, 2, "B")) == 0


def test_distance_does_not_use_euclidean_metric():
    # 三轴各差 1：欧氏 sqrt(3) 会大于 1，曼哈顿恰为 3 -> 临界合规。
    result = adjudicate([C(1, 1, 1, "A"), C(2, 2, 2, "B")])
    assert result.first_conflict is None
    assert result.pairs_compared == 1


def test_empty_and_single_box():
    result = adjudicate([])
    assert result.first_conflict is None
    assert result.pairs_compared == 0

    result = adjudicate([C(1, 1, 1, "A")])
    assert result.first_conflict is None
    assert result.pairs_compared == 0


@pytest.mark.parametrize(
    "a,b,required",
    [
        (C(1, 1, 1, "A"), C(1, 4, 1, "B"), 3),   # 列差 3，中间两格为空
        (C(1, 1, 1, "A"), C(2, 2, 2, "B"), 3),   # 排+列+层各差 1
        (C(3, 3, 3, "A"), C(3, 5, 3, "C"), 2),   # 列差 2
        (C(2, 2, 1, "B"), C(4, 2, 1, "D"), 2),   # 纯排差 2，临界合规
        (C(2, 2, 1, "B"), C(2, 2, 3, "D"), 2),   # 纯层差 2，临界合规
    ],
)
def test_critical_distance_equals_requirement_is_compliant(a, b, required):
    assert manhattan_distance(a, b) == required
    result = adjudicate([a, b])
    assert result.first_conflict is None


def test_critical_distance_with_layer_and_row_gaps():
    # B-D：排差 0、列差 0、层差 2 -> 恰好 2，临界合规。
    result = adjudicate([C(4, 4, 1, "B"), C(4, 4, 3, "D")])
    assert result.first_conflict is None


@pytest.mark.parametrize(
    "a,b,required",
    [
        # 距离比要求恰好低一格（位置不同，最小可能距离为 1）。
        (C(1, 1, 1, "A"), C(1, 3, 1, "B"), 3),   # 距离 2
        (C(1, 1, 1, "A"), C(2, 2, 1, "B"), 3),   # 排+列 = 2
        (C(1, 1, 1, "A"), C(2, 1, 2, "B"), 3),   # 排+层 = 2：平面上看似很远
        (C(1, 1, 1, "A"), C(1, 1, 2, "C"), 2),   # 层差 1
        (C(1, 1, 1, "B"), C(2, 1, 1, "D"), 2),   # 排差 1
    ],
)
def test_one_below_requirement_is_conflict(a, b, required):
    result = adjudicate([a, b])
    conflict = result.first_conflict
    assert conflict is not None
    assert conflict.required_distance == required
    assert conflict.actual_distance == manhattan_distance(a, b)
    assert conflict.actual_distance == required - 1


def test_conflict_orders_boxes_smaller_first_regardless_of_input():
    # 距离 2 < 3（A-B），输入时大坐标在前。
    a, b = C(5, 5, 5, "A"), C(4, 4, 5, "B")
    conflict = adjudicate([a, b]).first_conflict
    assert conflict is not None
    assert (conflict.smaller.row, conflict.smaller.col, conflict.smaller.tier) == (4, 4, 5)
    assert (conflict.larger.row, conflict.larger.col, conflict.larger.tier) == (5, 5, 5)


def test_first_conflict_sorted_by_smaller_position():
    boxes = [
        C(2, 5, 1, "A"), C(2, 5, 2, "C"),  # 冲突对 1，较小箱位 (2,5,1)
        C(1, 1, 4, "B"), C(1, 1, 5, "D"),  # 冲突对 2，较小箱位 (1,1,4) 应优先
        C(1, 1, 1, "A"), C(1, 1, 2, "C"),  # 冲突对 3，较小箱位 (1,1,1) 最优先
    ]
    conflict = adjudicate(boxes).first_conflict
    assert conflict is not None
    assert (conflict.smaller.row, conflict.smaller.col, conflict.smaller.tier) == (1, 1, 1)
    assert (conflict.larger.row, conflict.larger.col, conflict.larger.tier) == (1, 1, 2)


def test_first_conflict_tie_on_smaller_uses_larger_position_not_category():
    # A(3,3,3) 同时与 C(3,3,4)（要求 2）和 B(3,3,5)（要求 3）冲突。
    # 较小箱位相同，按较大箱位取 (3,3,4)，与类别无关。
    boxes = [C(3, 3, 3, "A"), C(3, 3, 5, "B"), C(3, 3, 4, "C")]
    conflict = adjudicate(boxes).first_conflict
    assert conflict is not None
    assert (conflict.larger.row, conflict.larger.col, conflict.larger.tier) == (3, 3, 4)
    assert conflict.actual_distance == 1
    assert conflict.required_distance == 2


def test_first_conflict_is_independent_of_input_order():
    boxes = [
        C(2, 5, 1, "A"), C(2, 5, 2, "C"),
        C(1, 1, 4, "B"), C(1, 1, 5, "D"),
        C(1, 1, 1, "A"), C(1, 1, 2, "C"),
    ]
    reference = adjudicate(boxes)

    rng = random.Random(20260915)
    for _ in range(20):
        shuffled = boxes[:]
        rng.shuffle(shuffled)
        result = adjudicate(shuffled)
        assert result.pairs_compared == reference.pairs_compared == 15
        got = result.first_conflict
        want = reference.first_conflict
        assert got is not None and want is not None
        assert (got.smaller.row, got.smaller.col, got.smaller.tier) == (
            want.smaller.row, want.smaller.col, want.smaller.tier,
        )
        assert (got.larger.row, got.larger.col, got.larger.tier) == (
            want.larger.row, want.larger.col, want.larger.tier,
        )
        assert got.actual_distance == want.actual_distance
        assert got.required_distance == want.required_distance


def test_empty_slots_between_boxes_do_not_shorten_distance():
    # 列 2、3 为空位不“折叠”距离：列差仍为 3，A-B 临界合规。
    result = adjudicate([C(1, 1, 1, "A"), C(1, 4, 1, "B")])
    assert result.first_conflict is None


def test_same_category_requires_only_one():
    # 同类相邻：距离 1，要求 1，合规。
    for cat in ["A", "B", "C", "D"]:
        result = adjudicate([C(1, 1, 1, cat), C(1, 1, 2, cat)])
        assert result.first_conflict is None


def test_pairs_compared_is_combination_count():
    boxes = [
        C(1, 1, 1, "A"), C(1, 1, 2, "D"),
        C(5, 5, 5, "B"), C(4, 4, 4, "C"),
    ]
    assert adjudicate(boxes).pairs_compared == 6
