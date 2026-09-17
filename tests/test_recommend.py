"""POST /api/v1/recommend-slot 接口与推荐搜索测试。

验收四组场景：
1. 最近层存在并列时，乱序输入仍选中 (排, 列, 层) 字典序首位；
2. 小舱无解返回无坐标状态 no_safe_slot；
3. 非法请求整份返回 422；
4. 原裁决接口行为不变（由 test_api.py / test_rules.py 既有用例保证，
   本文件最后一项用例做端到端复核）。
"""

import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Container
from app.rules import recommend_slot

client = TestClient(app)

URL = "/api/v1/recommend-slot"


def payload(**overrides):
    base = {
        "max_row": 10,
        "max_col": 10,
        "max_tier": 5,
        "containers": [],
        "category": "A",
        "desired": {"row": 5, "col": 5, "tier": 3},
    }
    base.update(overrides)
    return base


def box(row, col, tier, category):
    return {"row": row, "col": col, "tier": tier, "category": category}


def C(row: int, col: int, tier: int, category: str) -> Container:
    return Container(row=row, col=col, tier=tier, category=category)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 场景 1：最近层并列时取字典序首位，且与输入顺序无关
# ---------------------------------------------------------------------------

def tied_layout_containers():
    # C 占期望点 (2,2,2)：A-C 要求 2，距离 1 壳层全部不安全；
    # 两个 D 与待装 A 要求 1，不改变候选集合，仅用于制造多种输入顺序。
    return [box(2, 2, 2, "C"), box(3, 3, 3, "D"), box(3, 3, 1, "D")]


def test_tie_in_nearest_layer_picks_lexicographic_first():
    # 距离 2 壳层共 12 个候选并列，字典序最小为 (1,1,2)。
    response = client.post(URL, json=payload(
        max_row=3, max_col=3, max_tier=3,
        containers=tied_layout_containers(),
        desired={"row": 2, "col": 2, "tier": 2},
    ))
    assert response.status_code == 200
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 1, "col": 1, "tier": 2},
        "distance": 2,
    }


def test_tied_layer_result_is_independent_of_input_order():
    body = payload(
        max_row=3, max_col=3, max_tier=3,
        containers=tied_layout_containers(),
        desired={"row": 2, "col": 2, "tier": 2},
    )
    reference = client.post(URL, json=body).json()

    rng = random.Random(20260917)
    for _ in range(20):
        shuffled = tied_layout_containers()
        rng.shuffle(shuffled)
        response = client.post(URL, json={**body, "containers": shuffled})
        assert response.status_code == 200
        assert response.json() == reference


def test_rules_level_recommendation_is_order_independent():
    containers = [C(2, 2, 2, "C"), C(3, 3, 3, "D"), C(3, 3, 1, "D")]
    reference = recommend_slot(3, 3, 3, containers, "A", (2, 2, 2))
    assert reference is not None
    assert (reference.row, reference.col, reference.tier) == (1, 1, 2)
    assert reference.distance == 2

    rng = random.Random(20260917)
    for _ in range(20):
        shuffled = containers[:]
        rng.shuffle(shuffled)
        assert recommend_slot(3, 3, 3, shuffled, "A", (2, 2, 2)) == reference


# ---------------------------------------------------------------------------
# 场景 2：小舱无解 -> no_safe_slot，且不含坐标
# ---------------------------------------------------------------------------

def test_small_compartment_without_safe_slot_returns_status_only():
    # 2x2x1 小舱，B 占 (1,1,1)，待装 A：A-B 要求 3，
    # 舱内任意点到 (1,1,1) 的距离至多为 2，全部不安全。
    response = client.post(URL, json=payload(
        max_row=2, max_col=2, max_tier=1,
        containers=[box(1, 1, 1, "B")],
        desired={"row": 2, "col": 2, "tier": 1},
    ))
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "no_safe_slot"}
    assert "slot" not in body
    assert "distance" not in body


def test_fully_occupied_compartment_returns_no_safe_slot():
    containers = [box(r, c, 1, "D") for r in (1, 2) for c in (1, 2)]
    response = client.post(URL, json=payload(
        max_row=2, max_col=2, max_tier=1,
        containers=containers,
        category="D",
        desired={"row": 1, "col": 1, "tier": 1},
    ))
    assert response.status_code == 200
    assert response.json() == {"status": "no_safe_slot"}


def test_rules_level_no_safe_slot_returns_none():
    assert recommend_slot(2, 2, 1, [C(1, 1, 1, "B")], "A", (2, 2, 1)) is None


# ---------------------------------------------------------------------------
# 场景 3：非法请求整份 422
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_payload", [
    payload(desired={"row": 0, "col": 5, "tier": 3}),           # 期望坐标低于下界
    payload(desired={"row": 5, "col": 0, "tier": 3}),
    payload(desired={"row": 5, "col": 5, "tier": 0}),
    payload(desired={"row": 11, "col": 5, "tier": 3}),          # 期望坐标超出舱段
    payload(desired={"row": 5, "col": 11, "tier": 3}),
    payload(desired={"row": 5, "col": 5, "tier": 6}),
    payload(desired={"row": "5", "col": 5, "tier": 3}),         # 期望坐标类型错误
    payload(desired={"row": 5, "col": 5, "tier": 3.0}),
    payload(desired={"row": 5, "col": 5, "tier": True}),
    payload(desired={"row": 5, "col": 5}),                      # 期望坐标缺字段
    payload(desired={"row": 5, "col": 5, "tier": 3, "deck": 1}),  # 期望坐标多字段
    payload(category="E"),                                      # 未知待装类别
    payload(category="a"),                                      # 小写类别
    payload(category=1),                                        # 类别类型错误
    payload(max_row=0),                                         # 尺寸非正
    payload(max_col=-2),
    payload(max_tier="5"),                                      # 尺寸类型错误
    payload(containers=[box(11, 1, 1, "A")]),                   # 现存箱越界
    payload(containers=[box(1, 1, 1, "A"), box(1, 1, 1, "B")]),  # 重复占位
    payload(containers=[box(1, 1, 1, "E")]),                    # 现存箱未知类别
    payload(containers=[{"row": 1, "col": 1, "tier": 1, "category": "A", "weight": 9}]),
    {"max_row": 10, "max_col": 10, "max_tier": 5,
     "containers": [], "category": "A"},                        # 缺 desired
    {"max_row": 10, "max_col": 10, "max_tier": 5,
     "containers": [], "desired": {"row": 5, "col": 5, "tier": 3}},  # 缺 category
    payload(unknown_top_field=1),                               # 顶层多字段
    {},
    None,
])
def test_invalid_payloads_return_422_without_recommendation(bad_payload):
    response = client.post(URL, json=bad_payload)
    assert response.status_code == 422
    body = response.json()
    # FastAPI 校验错误结构，且绝不夹带推荐结果。
    assert "detail" in body
    assert "status" not in body
    assert "slot" not in body
    assert "distance" not in body


def test_multiple_problems_still_single_422():
    # 现存箱越界 + 重复占位 + 期望坐标越界 + 未知待装类别：整份拒绝。
    response = client.post(URL, json=payload(
        containers=[box(99, 1, 1, "A"), box(1, 1, 1, "A"), box(1, 1, 1, "B")],
        category="E",
        desired={"row": 99, "col": 1, "tier": 1},
    ))
    assert response.status_code == 422
    assert "status" not in response.json()


def test_method_not_allowed_for_get_on_endpoint():
    assert client.get(URL).status_code == 405


# ---------------------------------------------------------------------------
# 推荐语义：壳层展开、占用排除、全部现存箱均需满足、稀疏大舱不物化网格
# ---------------------------------------------------------------------------

def test_empty_compartment_recommends_desired_at_distance_zero():
    response = client.post(URL, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 5, "col": 5, "tier": 3},
        "distance": 0,
    }


def test_occupied_desired_moves_to_nearest_shell():
    # 期望点被 D 占用，待装 A：A-D 要求 1，距离 1 壳层全部安全，
    # 字典序最小为 (4,5,3)。
    response = client.post(URL, json=payload(containers=[box(5, 5, 3, "D")]))
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 4, "col": 5, "tier": 3},
        "distance": 1,
    }


def test_unsafe_desired_and_occupied_neighbour_are_both_skipped():
    # C 在 (5,5,2)（A-C 要求 2）：期望点 (5,5,3) 虽空但距离 1 不安全；
    # 距离 1 壳层中 (5,5,2) 被占用，其余安全，取字典序最小 (4,5,3)。
    response = client.post(URL, json=payload(containers=[box(5, 5, 2, "C")]))
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 4, "col": 5, "tier": 3},
        "distance": 1,
    }


def test_candidate_must_satisfy_every_existing_box():
    # 待装 A；C 在 (5,5,2)（要求 2），B 在 (4,5,3)（要求 3）。
    # 期望点距 C 仅 1 不安全；距离 1 壳层全部距 B 不足 3；
    # 距离 2 壳层中字典序最小且对两者都安全的是 (5,3,3)
    # （距 B 为 1+2+0=3，距 C 为 0+2+1=3，均临界合规）。
    response = client.post(URL, json=payload(
        containers=[box(5, 5, 2, "C"), box(4, 5, 3, "B")],
    ))
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 5, "col": 3, "tier": 3},
        "distance": 2,
    }


def test_shell_is_clipped_at_compartment_corner():
    # 期望点在角上 (1,1,1)，被 B 占用，待装 A：A-B 要求 3。
    # 壳层只在舱内生成，距离 3 层字典序最小为 (1,1,4)。
    result = recommend_slot(10, 10, 10, [C(1, 1, 1, "B")], "A", (1, 1, 1))
    assert result is not None
    assert (result.row, result.col, result.tier, result.distance) == (1, 1, 4, 3)


def test_huge_sparse_compartment_does_not_materialize_grid():
    # 10^6 x 10^6 x 10^6 空舱：物化完整网格将直接耗尽内存；
    # 壳层生成应在期望点即刻命中。
    result = recommend_slot(
        1_000_000, 1_000_000, 1_000_000, [], "A", (500_000, 500_000, 500_000)
    )
    assert result is not None
    assert (result.row, result.col, result.tier) == (500_000, 500_000, 500_000)
    assert result.distance == 0


def test_huge_compartment_with_occupied_desired_searches_single_shell():
    # 期望点被同类箱占用（要求 1）：距离 1 壳层即安全，无需遍历全舱。
    result = recommend_slot(
        1_000_000, 1_000_000, 1_000_000,
        [C(500_000, 500_000, 500_000, "A")], "A",
        (500_000, 500_000, 500_000),
    )
    assert result is not None
    assert (result.row, result.col, result.tier, result.distance) == (
        499_999, 500_000, 500_000, 1,
    )


# ---------------------------------------------------------------------------
# 场景 4：原裁决接口行为不变（端到端复核，细粒度用例见 test_api.py）
# ---------------------------------------------------------------------------

def test_adjudicate_endpoint_unchanged_after_recommend_slot_added():
    # 临界合规：A-B 距离恰为 3。
    compliant = client.post("/api/v1/adjudicate", json={
        "max_row": 10, "max_col": 10, "max_tier": 5,
        "containers": [box(1, 1, 1, "A"), box(1, 4, 1, "B")],
    })
    assert compliant.status_code == 200
    assert compliant.json() == {"status": "compliant", "pairs_compared": 1}

    # 首冲突顺序与比较对数不变。
    conflict = client.post("/api/v1/adjudicate", json={
        "max_row": 10, "max_col": 10, "max_tier": 5,
        "containers": [
            box(2, 5, 1, "A"), box(2, 5, 2, "C"),
            box(1, 1, 4, "B"), box(1, 1, 5, "D"),
            box(1, 1, 1, "A"), box(1, 1, 2, "C"),
        ],
    })
    assert conflict.status_code == 200
    data = conflict.json()
    assert data["status"] == "conflict"
    evidence = data["first_conflict"]
    assert (evidence["container_a"]["row"], evidence["container_a"]["col"],
            evidence["container_a"]["tier"]) == (1, 1, 1)
    assert (evidence["container_b"]["row"], evidence["container_b"]["col"],
            evidence["container_b"]["tier"]) == (1, 1, 2)
    assert evidence["actual_distance"] == 1
    assert evidence["required_distance"] == 2
