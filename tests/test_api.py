"""FastAPI 端到端测试。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

URL = "/api/v1/adjudicate"


def payload(**overrides):
    base = {
        "max_row": 10,
        "max_col": 10,
        "max_tier": 5,
        "containers": [],
    }
    base.update(overrides)
    return base


def box(row, col, tier, category):
    return {"row": row, "col": col, "tier": tier, "category": category}


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_layout_is_compliant_zero_pairs():
    response = client.post(URL, json=payload())
    assert response.status_code == 200
    assert response.json() == {"status": "compliant", "pairs_compared": 0}


def test_compliant_response_reports_pair_count():
    response = client.post(URL, json=payload(containers=[
        box(1, 1, 1, "A"),
        box(1, 4, 1, "B"),   # 距离 3，临界合规
        box(10, 10, 5, "C"),
        box(9, 10, 5, "C"),   # 同类距离 1，合规
    ]))
    assert response.status_code == 200
    assert response.json() == {"status": "compliant", "pairs_compared": 6}


def test_conflict_response_shape_and_values():
    response = client.post(URL, json=payload(containers=[
        box(5, 5, 5, "B"),
        box(4, 4, 4, "A"),   # 曼哈顿距离 3？4+4+4 轴差各 1 => 3，合规
        box(4, 4, 5, "A"),   # 与 B(5,5,5) 距离 2 < 3 冲突
    ]))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "conflict"
    evidence = data["first_conflict"]
    assert set(evidence) == {"container_a", "container_b", "actual_distance", "required_distance"}
    # 较小箱位在前
    assert evidence["container_a"]["row"] == 4
    assert evidence["container_a"]["col"] == 4
    assert evidence["container_a"]["tier"] == 5
    assert evidence["container_b"]["row"] == 5
    assert evidence["container_b"]["col"] == 5
    assert evidence["container_b"]["tier"] == 5
    assert evidence["actual_distance"] == 2
    assert evidence["required_distance"] == 3


def test_boundary_coordinates_accepted():
    response = client.post(URL, json=payload(
        max_row=3, max_col=4, max_tier=2,
        containers=[box(1, 1, 1, "A"), box(3, 4, 2, "B")],
    ))
    assert response.status_code == 200
    assert response.json()["status"] == "compliant"


@pytest.mark.parametrize("bad_payload", [
    payload(max_row=0),
    payload(max_col=-1),
    payload(max_tier=0),
    payload(containers=[box(0, 1, 1, "A")]),
    payload(containers=[box(1, 0, 1, "A")]),
    payload(containers=[box(1, 1, 0, "A")]),
    payload(containers=[box(11, 1, 1, "A")]),
    payload(containers=[box(1, 11, 1, "A")]),
    payload(containers=[box(1, 1, 6, "A")]),
    payload(containers=[box(1, 1, 1, "E")]),
    payload(containers=[box(1, 1, 1, "a")]),
    payload(containers=[
        box(1, 1, 1, "A"),
        box(1, 1, 1, "B"),
    ]),
    {"max_row": 10, "max_col": 10, "max_tier": 5},  # 缺 containers
    {"max_row": "10", "max_col": 10, "max_tier": 5, "containers": []},  # 尺寸类型错误
    payload(containers=[{"row": "1", "col": 1, "tier": 1, "category": "A"}]),
    payload(containers=[{"row": 1, "col": 1, "tier": 1, "category": "A", "weight": 99}]),
    payload(containers=[{"row": 1, "col": 1, "tier": 1}]),  # 缺 category
    payload(containers=[{"row": 1, "col": 1, "category": "A"}]),  # 缺 tier
    payload(unknown_top_field=1),
    {},
    None,
])
def test_invalid_payloads_return_422_without_partial_verdict(bad_payload):
    response = client.post(URL, json=bad_payload)
    assert response.status_code == 422
    body = response.json()
    # FastAPI 校验错误结构，且绝不夹带裁决结果。
    assert "detail" in body
    assert "status" not in body
    assert "first_conflict" not in body
    assert "pairs_compared" not in body


def test_multiple_problems_still_single_422():
    # 同时越界 + 重复 + 未知类别：整份拒绝，不做任何裁决。
    response = client.post(URL, json=payload(containers=[
        box(99, 1, 1, "A"),
        box(1, 1, 1, "E"),
        box(1, 1, 1, "A"),
        box(1, 1, 1, "B"),
    ]))
    assert response.status_code == 422
    assert "status" not in response.json()


def test_shuffled_input_gives_identical_conflict():
    boxes = [
        box(2, 5, 1, "A"), box(2, 5, 2, "C"),
        box(1, 1, 4, "B"), box(1, 1, 5, "D"),
        box(1, 1, 1, "A"), box(1, 1, 2, "C"),
    ]
    first = client.post(URL, json=payload(containers=boxes)).json()
    second = client.post(URL, json=payload(containers=list(reversed(boxes)))).json()
    assert first == second
    assert first["first_conflict"]["container_a"]["row"] == 1


@pytest.mark.parametrize("cat_a,cat_b", [
    ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"),
    ("A", "D"), ("B", "C"), ("C", "D"),
])
def test_non_special_pairs_adjacent_are_compliant(cat_a, cat_b):
    response = client.post(URL, json=payload(containers=[
        box(1, 1, 1, cat_a),
        box(1, 1, 2, cat_b),  # 距离 1
    ]))
    assert response.status_code == 200
    assert response.json()["status"] == "compliant"


def test_method_not_allowed_for_get_on_endpoint():
    assert client.get(URL).status_code == 405
