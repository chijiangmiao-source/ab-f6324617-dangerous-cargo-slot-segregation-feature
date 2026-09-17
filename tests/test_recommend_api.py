"""POST /api/v1/recommend-slot 端到端测试。

自动化验收限定四组场景，本文件覆盖其中与新接口相关的三组：
1. 最近层并列时，乱序输入仍选中字典序首位；
2. 小舱无安全位置 -> no_safe_slot 且不含坐标；
3. 非法请求整份 422（类型错误、越界、重复占位、未知类别）。
原裁决接口的用例继续在 test_api.py / test_rules.py 中保证不变。
"""

import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

URL = "/api/v1/recommend-slot"


def box(row, col, tier, category):
    return {"row": row, "col": col, "tier": tier, "category": category}


def payload(**overrides):
    base = {
        "max_row": 10,
        "max_col": 10,
        "max_tier": 5,
        "containers": [],
        "cargo_category": "A",
        "expected_row": 5,
        "expected_col": 5,
        "expected_tier": 2,
    }
    base.update(overrides)
    return base


# ---------- 场景 1：最近层并列 -> 字典序首位，乱序输入不变 ----------

def test_expected_point_free_is_recommended_at_distance_zero():
    response = client.post(URL, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 5, "col": 5, "tier": 2},
        "search_distance": 0,
    }


def test_tie_at_nearest_layer_picks_lexicographic_first():
    # 期望点 (3,3,2) 被占；距离 1 的六个邻居全部并列安全，
    # 必须取字典序首位 (2,3,2)，与类别无关。
    body = payload(
        max_row=8, max_col=8, max_tier=5,
        containers=[box(3, 3, 2, "A")],
        cargo_category="D",
        expected_row=3, expected_col=3, expected_tier=2,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 2, "col": 3, "tier": 2},
        "search_distance": 1,
    }


def _required(cat_a, cat_b):
    special = {frozenset("AB"): 3, frozenset("AC"): 2, frozenset("BD"): 2}
    return special.get(frozenset({cat_a, cat_b}), 1)


def brute_force_slot(sizes, existing, cargo_category, expected):
    """验收侧独立暴力扫描：全舱按壳层 -> 字典序找首个安全空位。"""
    max_row, max_col, max_tier = sizes
    occupied = {(b["row"], b["col"], b["tier"]) for b in existing}
    max_distance = (
        max(expected[0] - 1, max_row - expected[0])
        + max(expected[1] - 1, max_col - expected[1])
        + max(expected[2] - 1, max_tier - expected[2])
    )

    def safe(pos):
        r, c, t = pos
        return all(
            abs(r - b["row"]) + abs(c - b["col"]) + abs(t - b["tier"])
            >= _required(cargo_category, b["category"])
            for b in existing
        )

    for distance in range(max_distance + 1):
        candidates = sorted(
            (r, c, t)
            for r in range(1, max_row + 1)
            for c in range(1, max_col + 1)
            for t in range(1, max_tier + 1)
            if abs(r - expected[0]) + abs(c - expected[1]) + abs(t - expected[2]) == distance
            and (r, c, t) not in occupied
        )
        for pos in candidates:
            if safe(pos):
                return {"status": "recommended",
                        "slot": {"row": pos[0], "col": pos[1], "tier": pos[2]},
                        "search_distance": distance}
    return {"status": "no_safe_slot"}


def test_shuffled_existing_containers_give_identical_recommendation():
    # 期望点 (3,4,2) 被一个 B 箱占据：待装 A 对它要求 3，距离 1、2 的
    # 壳层全部被否，必须走到更远壳层；其余现存箱分布各特殊类别对，
    # 答案与验收侧独立暴力扫描完全一致，且与输入顺序无关。
    sizes = (7, 7, 4)
    containers = [
        box(3, 4, 2, "B"),  # 占据期望点（A-B 要求 3）
        box(1, 1, 1, "B"), box(1, 6, 1, "C"),
        box(6, 1, 3, "D"), box(6, 6, 3, "A"),
        box(3, 3, 1, "C"), box(4, 4, 3, "B"),
    ]
    expected = (3, 4, 2)
    want = brute_force_slot(sizes, containers, "A", expected)
    assert want["status"] == "recommended"
    assert want["search_distance"] == 3  # 确实被迫走到了第 3 层

    base = payload(
        max_row=sizes[0], max_col=sizes[1], max_tier=sizes[2],
        cargo_category="A",
        expected_row=expected[0], expected_col=expected[1], expected_tier=expected[2],
    )

    first = client.post(URL, json={**base, "containers": containers})
    assert first.status_code == 200
    assert first.json() == want

    rng = random.Random(20260917)
    for _ in range(10):
        shuffled = containers[:]
        rng.shuffle(shuffled)
        later = client.post(URL, json={**base, "containers": shuffled})
        assert later.status_code == 200
        assert later.json() == want


def test_random_layouts_match_brute_force_and_ignore_input_order():
    # 随机布局批量交叉验证：服务结果恒等于独立暴力扫描，乱序输入不变。
    rng = random.Random(20260917)
    categories = ["A", "B", "C", "D"]
    for _ in range(40):
        sizes = (rng.randint(1, 6), rng.randint(1, 6), rng.randint(1, 4))
        occupied: set[tuple[int, int, int]] = set()
        containers = []
        for _ in range(rng.randint(0, sizes[0] * sizes[1] * sizes[2])):
            pos = (rng.randint(1, sizes[0]), rng.randint(1, sizes[1]),
                   rng.randint(1, sizes[2]))
            if pos in occupied:
                continue
            occupied.add(pos)
            containers.append(box(pos[0], pos[1], pos[2], rng.choice(categories)))
        expected = (rng.randint(1, sizes[0]), rng.randint(1, sizes[1]),
                    rng.randint(1, sizes[2]))
        cargo = rng.choice(categories)
        want = brute_force_slot(sizes, containers, cargo, expected)

        body = payload(
            max_row=sizes[0], max_col=sizes[1], max_tier=sizes[2],
            containers=containers, cargo_category=cargo,
            expected_row=expected[0], expected_col=expected[1],
            expected_tier=expected[2],
        )
        response = client.post(URL, json=body)
        assert response.status_code == 200
        assert response.json() == want

        shuffled = containers[:]
        rng.shuffle(shuffled)
        response = client.post(URL, json={**body, "containers": shuffled})
        assert response.json() == want


def test_recommendation_respects_special_pair_distances():
    # 待装 A：对 B 要求 3、对 C 要求 2。期望点被 B 占据，最近两层全部
    # 不满足 3；距离 3 的字典序首位又被 C 占据，随后 (2,3,2) 恰好
    # 对两者临界达标。
    body = payload(
        max_row=8, max_col=8, max_tier=5,
        containers=[box(4, 4, 2, "B"), box(1, 4, 2, "C")],
        cargo_category="A",
        expected_row=4, expected_col=4, expected_tier=2,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    assert response.json() == {
        "status": "recommended",
        "slot": {"row": 2, "col": 3, "tier": 2},
        "search_distance": 3,
    }


def test_recommendation_does_not_modify_existing_slots():
    containers = [box(3, 3, 2, "A")]
    body = payload(
        max_row=8, max_col=8, max_tier=5,
        containers=containers,
        cargo_category="D",
        expected_row=3, expected_col=3, expected_tier=2,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    # 请求中的现存箱位原样保留，响应也不回传任何“搬移后”布局。
    assert containers == [box(3, 3, 2, "A")]
    assert set(response.json()) == {"status", "slot", "search_distance"}


# ---------- 场景 2：小舱无解 -> no_safe_slot，不含坐标 ----------

def test_single_cell_occupied_returns_no_safe_slot_without_coordinates():
    body = payload(
        max_row=1, max_col=1, max_tier=1,
        containers=[box(1, 1, 1, "A")],
        cargo_category="B",
        expected_row=1, expected_col=1, expected_tier=1,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "no_safe_slot"}
    assert "slot" not in data
    assert "row" not in data
    assert "search_distance" not in data


def test_isolation_rules_can_make_every_free_cell_unsafe():
    # 3x1x1：B 居中，待装 A 要求 3，两端空位距离都只有 1。
    body = payload(
        max_row=3, max_col=1, max_tier=1,
        containers=[box(2, 1, 1, "B")],
        cargo_category="A",
        expected_row=2, expected_col=1, expected_tier=1,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    assert response.json() == {"status": "no_safe_slot"}


def test_full_hold_returns_no_safe_slot():
    body = payload(
        max_row=2, max_col=1, max_tier=1,
        containers=[box(1, 1, 1, "A"), box(2, 1, 1, "C")],
        cargo_category="B",
        expected_row=1, expected_col=1, expected_tier=1,
    )
    response = client.post(URL, json=body)
    assert response.status_code == 200
    assert response.json() == {"status": "no_safe_slot"}


# ---------- 场景 3：非法请求整份 422 ----------

INVALID_PAYLOADS = [
    # 类型错误：尺寸、坐标、类别均拒绝字符串/浮点/布尔
    payload(max_row="8"),
    payload(max_col=1.5),
    payload(max_tier=True),
    payload(cargo_category=1),
    payload(expected_row="3"),
    payload(expected_col=2.0),
    payload(expected_tier=False),
    payload(containers=[{"row": "3", "col": 3, "tier": 2, "category": "A"}]),
    # 尺寸非正
    payload(max_row=0),
    payload(max_col=-1),
    # 现存箱坐标越界（下界/上界）
    payload(containers=[box(0, 1, 1, "A")]),
    payload(containers=[box(11, 1, 1, "A")]),
    payload(containers=[box(1, 0, 1, "A")]),
    payload(containers=[box(1, 1, 6, "A")]),
    # 期望坐标越界（下界/上界，三个轴向）
    payload(expected_row=0),
    payload(expected_row=11),
    payload(expected_col=11),
    payload(expected_tier=6),
    # 重复占位
    payload(containers=[box(1, 1, 1, "A"), box(1, 1, 1, "B")]),
    # 未知类别：现存箱与待装箱都拒绝
    payload(containers=[box(1, 1, 1, "E")]),
    payload(cargo_category="e"),
    payload(cargo_category="AB"),
    # 缺字段
    {k: v for k, v in payload().items() if k != "containers"},
    {k: v for k, v in payload().items() if k != "cargo_category"},
    {k: v for k, v in payload().items() if k != "expected_row"},
    {},
    None,
    # 多字段（顶层与箱位层）
    payload(extra_field=1),
    payload(containers=[{**box(1, 1, 1, "A"), "weight": 9}]),
]


def test_all_invalid_payloads_return_single_422():
    for bad in INVALID_PAYLOADS:
        response = client.post(URL, json=bad)
        assert response.status_code == 422, f"应 422 的请求被受理: {bad}"
        body = response.json()
        # 标准错误结构，绝不夹带任何寻位结果。
        assert "detail" in body
        assert "status" not in body
        assert "slot" not in body
        assert "search_distance" not in body


def test_multiple_problems_still_single_422():
    # 现存箱越界 + 重复 + 未知类别，且期望坐标越界：一次 422 聚合拒绝。
    bad = payload(
        max_row=5, max_col=5, max_tier=3,
        containers=[
            box(99, 1, 1, "A"),
            box(1, 1, 1, "E"),
            box(1, 1, 1, "A"),
            box(1, 1, 1, "B"),
        ],
        cargo_category="A",
        expected_row=6, expected_col=1, expected_tier=1,
    )
    response = client.post(URL, json=bad)
    assert response.status_code == 422
    assert "status" not in response.json()


def test_method_not_allowed_for_get():
    assert client.get(URL).status_code == 405


def test_openapi_discriminator_documents_both_statuses():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/recommend-slot" in schema["paths"]
