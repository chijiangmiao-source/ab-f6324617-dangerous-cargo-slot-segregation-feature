#!/usr/bin/env python3
"""一次性验收脚本（verify 服务入口）。

对运行中的裁决服务执行一组固定验收用例，全部通过则进程以 0 退出，
否则输出失败明细并以 1 退出。用例覆盖：

* 健康地址 /healthz、/health、/ 均返回 200 且 status=ok（Docker 健康检查
  所用地址不得 404），且容器健康检查脚本对健康服务退出码为 0；
* 三类特殊规则的临界距离（恰好等于要求 -> 合规），且刻意使用排差/列差/
  层差组合，证明使用的是三轴曼哈顿距离而非欧氏距离、不忽略中间空位；
* 临界再低一格 -> 唯一冲突，返回证据由本脚本独立按曼哈顿距离复算；
* 多冲突时首项按 (较小箱位, 较大箱位) 排序，且与输入顺序无关；
* 全部非法输入（尺寸非正、越界、重复、未知类别、缺字段、多字段）整份 422；
* 无冲突时返回比较对数 C(n, 2)；
* POST /api/v1/recommend-slot 三组场景：最近壳层并列时乱序输入仍取
  字典序首位（结果由脚本侧独立暴力扫描复算）、小舱无安全位置时只回
  no_safe_slot 状态且不含坐标、非法请求整份 422。

环境变量：
    VERIFY_BASE_URL  被测服务地址，默认 http://api:8000（Compose 网络）。
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("VERIFY_BASE_URL", "http://api:8000").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/v1/adjudicate"
RECOMMEND_ENDPOINT = f"{BASE_URL}/api/v1/recommend-slot"
HEALTHCHECK_SCRIPT = Path(__file__).resolve().parent / "healthcheck.py"
TIMEOUT = 10.0

failures: list[str] = []
checks_run = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def manhattan(p: dict[str, Any], q: dict[str, Any]) -> int:
    """验收侧独立复算：排差 + 列差 + 层差。"""
    return abs(p["row"] - q["row"]) + abs(p["col"] - q["col"]) + abs(p["tier"] - q["tier"])


def box(row: int, col: int, tier: int, category: str) -> dict[str, Any]:
    return {"row": row, "col": col, "tier": tier, "category": category}


def layout(containers: list[dict[str, Any]], **sizes: Any) -> dict[str, Any]:
    return {
        "max_row": sizes.get("max_row", 20),
        "max_col": sizes.get("max_col", 20),
        "max_tier": sizes.get("max_tier", 10),
        "containers": containers,
    }


def post(client: httpx.Client, body: dict[str, Any]) -> httpx.Response:
    return client.post(ENDPOINT, json=body, timeout=TIMEOUT)


def expect_compliant(client: httpx.Client, name: str, containers: list[dict[str, Any]],
                     expected_pairs: int | None = None, **sizes: Any) -> None:
    response = post(client, layout(containers, **sizes))
    ok_status = response.status_code == 200
    check(f"{name} [HTTP 200]", ok_status, f"got {response.status_code} {response.text}")
    if not ok_status:
        return
    data = response.json()
    check(f"{name} [status=compliant]", data.get("status") == "compliant", str(data))
    if expected_pairs is not None:
        n = len(containers)
        check(f"{name} [pairs_compared={expected_pairs}]",
              data.get("pairs_compared") == expected_pairs == n * (n - 1) // 2,
              str(data))


def expect_conflict(client: httpx.Client, name: str, containers: list[dict[str, Any]],
                    required: int, actual: int | None = None) -> dict[str, Any] | None:
    response = post(client, layout(containers))
    check(f"{name} [HTTP 200]", response.status_code == 200,
          f"got {response.status_code} {response.text}")
    if response.status_code != 200:
        return None
    data = response.json()
    check(f"{name} [status=conflict]", data.get("status") == "conflict", str(data))
    evidence = data.get("first_conflict")
    check(f"{name} [证据字段完整]",
          evidence is not None and set(evidence) == {
              "container_a", "container_b", "actual_distance", "required_distance"},
          str(evidence))
    if not evidence:
        return None

    a, b = evidence["container_a"], evidence["container_b"]
    smaller = (a["row"], a["col"], a["tier"])
    larger = (b["row"], b["col"], b["tier"])
    check(f"{name} [较小箱位排在 container_a]", smaller < larger, f"{smaller} !< {larger}")

    recomputed = manhattan(a, b)
    check(f"{name} [实际距离可独立复算]",
          evidence["actual_distance"] == recomputed,
          f"服务返回 {evidence['actual_distance']}，复算 {recomputed}")
    check(f"{name} [要求距离={required}]",
          evidence["required_distance"] == required,
          f"got {evidence['required_distance']}")
    check(f"{name} [实际距离={actual if actual is not None else required - 1} 且低于要求]",
          evidence["actual_distance"] == (actual if actual is not None else required - 1)
          and evidence["actual_distance"] < evidence["required_distance"],
          str(evidence))
    return data


def expect_422(client: httpx.Client, name: str, body: Any) -> None:
    response = client.post(ENDPOINT, json=body, timeout=TIMEOUT)
    check(f"{name} [HTTP 422]", response.status_code == 422,
          f"got {response.status_code} {response.text}")
    if response.status_code == 422:
        data = response.json()
        check(f"{name} [无任何裁决字段]",
              "status" not in data and "first_conflict" not in data
              and "pairs_compared" not in data and "detail" in data,
              str(data))


# ------------------------------------------------------------------
# recommend-slot 验收辅助
# ------------------------------------------------------------------

_SPECIAL_REQUIREMENTS = {
    frozenset({"A", "B"}): 3,
    frozenset({"A", "C"}): 2,
    frozenset({"B", "D"}): 2,
}


def _required(cat_a: str, cat_b: str) -> int:
    return _SPECIAL_REQUIREMENTS.get(frozenset({cat_a, cat_b}), 1)


def recommend_body(containers: list[dict[str, Any]], cargo_category: str,
                   expected: tuple[int, int, int], **sizes: Any) -> dict[str, Any]:
    return {
        "max_row": sizes.get("max_row", 20),
        "max_col": sizes.get("max_col", 20),
        "max_tier": sizes.get("max_tier", 10),
        "containers": containers,
        "cargo_category": cargo_category,
        "expected_row": expected[0],
        "expected_col": expected[1],
        "expected_tier": expected[2],
    }


def brute_force_slot(sizes: tuple[int, int, int],
                     existing: list[dict[str, Any]],
                     cargo_category: str,
                     expected: tuple[int, int, int]) -> dict[str, Any]:
    """验收侧独立复算：按壳层（曼哈顿距离）-> 同层 (排,列,层) 字典序扫描。"""
    max_row, max_col, max_tier = sizes
    occupied = {(b["row"], b["col"], b["tier"]) for b in existing}
    max_distance = (
        max(expected[0] - 1, max_row - expected[0])
        + max(expected[1] - 1, max_col - expected[1])
        + max(expected[2] - 1, max_tier - expected[2])
    )

    def is_safe(pos: tuple[int, int, int]) -> bool:
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
            if is_safe(pos):
                return {"status": "recommended",
                        "slot": {"row": pos[0], "col": pos[1], "tier": pos[2]},
                        "search_distance": distance}
    return {"status": "no_safe_slot"}


def expect_recommended(client: httpx.Client, name: str, body: dict[str, Any],
                       want: dict[str, Any]) -> None:
    response = client.post(RECOMMEND_ENDPOINT, json=body, timeout=TIMEOUT)
    check(f"{name} [HTTP 200]", response.status_code == 200,
          f"got {response.status_code} {response.text}")
    if response.status_code != 200 or not want:
        return
    data = response.json()
    check(f"{name} [与独立暴力扫描完全一致]", data == want,
          f"服务 {data} != 复算 {want}")


def expect_recommend_422(client: httpx.Client, name: str, body: Any) -> None:
    response = client.post(RECOMMEND_ENDPOINT, json=body, timeout=TIMEOUT)
    check(f"{name} [HTTP 422]", response.status_code == 422,
          f"got {response.status_code} {response.text}")
    if response.status_code == 422:
        data = response.json()
        check(f"{name} [无任何推荐字段]",
              "status" not in data and "slot" not in data
              and "search_distance" not in data and "detail" in data,
              str(data))


def main() -> int:
    print(f"验收目标: {ENDPOINT}")
    with httpx.Client() as client:
        # 0. 健康地址（Docker 健康检查与直接访问均不得 404）
        for path in ("/healthz", "/health", "/"):
            response = client.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
            ok = response.status_code == 200 and response.json().get("status") == "ok"
            check(f"GET {path} -> 200 且 status=ok", ok,
                  f"{response.status_code} {response.text}")

        # 以容器健康检查所用脚本对运行中的服务探测，退出码必须为 0。
        script_env = {**os.environ, "HEALTHCHECK_URL": f"{BASE_URL}/healthz"}
        result = subprocess.run(
            [sys.executable, str(HEALTHCHECK_SCRIPT)],
            env=script_env, capture_output=True, timeout=TIMEOUT,
        )
        check("scripts/healthcheck.py 对健康服务退出码为 0",
              result.returncode == 0, result.stderr.decode())

        print("\n[1] 临界距离必须判为合规（实际 == 要求）")
        # A-B 要求 3
        expect_compliant(client, "A-B 纯列差=3（中间两格空位不折叠距离）",
                         [box(1, 1, 1, "A"), box(1, 4, 1, "B")], expected_pairs=1)
        expect_compliant(client, "A-B 排+列+层各差 1（曼哈顿 3，非欧氏 sqrt3 判定）",
                         [box(1, 1, 1, "A"), box(2, 2, 2, "B")], expected_pairs=1)
        expect_compliant(client, "A-B 排差 1+层差 2=3",
                         [box(3, 5, 1, "A"), box(4, 5, 3, "B")], expected_pairs=1)
        # A-C 要求 2
        expect_compliant(client, "A-C 纯层差=2",
                         [box(3, 3, 2, "A"), box(3, 3, 4, "C")], expected_pairs=1)
        expect_compliant(client, "A-C 列差 1+层差 1=2",
                         [box(3, 3, 2, "A"), box(3, 4, 3, "C")], expected_pairs=1)
        # B-D 要求 2
        expect_compliant(client, "B-D 纯排差=2",
                         [box(4, 1, 1, "B"), box(6, 1, 1, "D")], expected_pairs=1)
        expect_compliant(client, "B-D 排差 1+列差 1=2",
                         [box(4, 1, 1, "B"), box(5, 2, 1, "D")], expected_pairs=1)
        # 其余组合（含同类）要求 1：相邻即合规
        expect_compliant(client, "A-A 相邻（同类要求 1）",
                         [box(1, 1, 1, "A"), box(1, 1, 2, "A")], expected_pairs=1)
        expect_compliant(client, "A-D 相邻（其余组合要求 1）",
                         [box(1, 1, 1, "A"), box(2, 1, 1, "D")], expected_pairs=1)
        expect_compliant(client, "C-D 相邻（其余组合要求 1）",
                         [box(1, 1, 1, "C"), box(1, 2, 1, "D")], expected_pairs=1)
        # 边界坐标恰好等于舱段容量
        expect_compliant(client, "坐标恰好取到上界 (3,4,2)",
                         [box(1, 1, 1, "A"), box(3, 4, 2, "B")], expected_pairs=1,
                         max_row=3, max_col=4, max_tier=2)

        print("\n[2] 临界再低一格 -> 唯一冲突，证据可复算")
        expect_conflict(client, "A-B 列差=2（再低一格）",
                        [box(1, 1, 1, "A"), box(1, 3, 1, "B")], required=3)
        expect_conflict(client, "A-B 排差 1+层差 1=2（层差排差叠加，看似分散仍冲突）",
                        [box(1, 5, 1, "A"), box(2, 5, 2, "B")], required=3, actual=2)
        expect_conflict(client, "A-C 纯层差=1",
                        [box(3, 3, 2, "A"), box(3, 3, 3, "C")], required=2)
        expect_conflict(client, "B-D 纯排差=1",
                        [box(4, 1, 1, "B"), box(5, 1, 1, "D")], required=2)
        # 要求为 1 的组合：坐标唯一使最小实际距离也是 1，故不存在冲突，无可触发项。

        print("\n[3] 多冲突首项排序唯一，且与输入顺序无关")
        many = [
            box(2, 5, 1, "A"), box(2, 5, 2, "C"),  # 冲突对，较小 (2,5,1)
            box(1, 1, 4, "B"), box(1, 1, 5, "D"),  # 冲突对，较小 (1,1,4)
            box(1, 1, 1, "A"), box(1, 1, 2, "C"),  # 冲突对，较小 (1,1,1) 应取为首项
        ]
        first = expect_conflict(client, "多冲突首项为 (1,1,1)-(1,1,2)", many,
                                required=2, actual=1)
        if first:
            ev = first["first_conflict"]
            ordered = (
                (ev["container_a"]["row"], ev["container_a"]["col"], ev["container_a"]["tier"]) == (1, 1, 1)
                and (ev["container_b"]["row"], ev["container_b"]["col"], ev["container_b"]["tier"]) == (1, 1, 2)
            )
            check("首项双方坐标符合 (较小, 较大) 字典序", ordered, str(ev))

        reversed_response = post(client, layout(list(reversed(many))))
        check("逆序输入返回完全相同的 JSON",
              reversed_response.status_code == 200 and reversed_response.json() == first,
              f"{reversed_response.text} != {first}")

        print("\n[4] 无冲突返回比较对数")
        expect_compliant(client, "空舱 0 对", [], expected_pairs=0)
        expect_compliant(client, "单箱 0 对", [box(1, 1, 1, "A")], expected_pairs=0)
        expect_compliant(client, "4 箱全部合规 -> C(4,2)=6 对",
                         [box(1, 1, 1, "A"), box(1, 4, 1, "B"),
                          box(10, 10, 5, "C"), box(9, 10, 5, "C")],
                         expected_pairs=6)

        print("\n[5] 非法输入整份 422，不产生部分裁决")
        good = layout([])
        expect_422(client, "max_row=0", {**good, "max_row": 0})
        expect_422(client, "max_col=-3", {**good, "max_col": -3})
        expect_422(client, "max_tier=0", {**good, "max_tier": 0})
        expect_422(client, "排坐标 0（下界越界）", layout([box(0, 1, 1, "A")]))
        expect_422(client, "层坐标超出上界", layout([box(1, 1, 11, "A")]))
        expect_422(client, "列坐标超出上界", layout([box(1, 21, 1, "A")]))
        expect_422(client, "未知类别 E", layout([box(1, 1, 1, "E")]))
        expect_422(client, "小写类别 a", layout([box(1, 1, 1, "a")]))
        expect_422(client, "重复箱位", layout([box(1, 1, 1, "A"), box(1, 1, 1, "B")]))
        expect_422(client, "越界+重复+未知类别同时出现",
                   layout([box(99, 1, 1, "A"), box(1, 1, 1, "E"),
                           box(1, 1, 1, "A"), box(1, 1, 1, "B")]))
        expect_422(client, "尺寸为字符串", {**good, "max_row": "10"})
        expect_422(client, "坐标为字符串", layout([box("1", 1, 1, "A")]))
        expect_422(client, "缺少 containers 字段",
                   {"max_row": 10, "max_col": 10, "max_tier": 10})
        expect_422(client, "箱位缺少 category",
                   layout([{"row": 1, "col": 1, "tier": 1}]))
        expect_422(client, "箱位含未声明字段",
                   layout([{**box(1, 1, 1, "A"), "weight": 99}]))
        expect_422(client, "顶层含未声明字段", {**good, "ship": "x"})
        expect_422(client, "空 JSON", {})

        print("\n[6] recommend-slot：最近层并列时字典序首位 + 乱序不变")
        # 期望点 (3,4,2) 被 B 占据（待装 A 对它要求 3）：距离 1、2 的
        # 壳层全部不达标，必须走到第 3 层，候选同时受 C 距离 2 的限制。
        sizes = (7, 7, 4)
        existing = [
            box(3, 4, 2, "B"),
            box(1, 1, 1, "B"), box(1, 6, 1, "C"),
            box(6, 1, 3, "D"), box(6, 6, 3, "A"),
            box(3, 3, 1, "C"), box(4, 4, 3, "B"),
        ]
        expected_xyz = (3, 4, 2)
        want = brute_force_slot(sizes, existing, "A", expected_xyz)
        check("独立暴力扫描确认最近可行层为第 3 层",
              want.get("status") == "recommended" and want.get("search_distance") == 3,
              str(want))

        recommend_req = recommend_body(existing, "A", expected_xyz,
                                       max_row=sizes[0], max_col=sizes[1],
                                       max_tier=sizes[2])
        expect_recommended(client, "服务结果与独立复算一致", recommend_req, want)

        # 同层并列取字典序首位已含在 want 的扫描方式中；再验证乱序输入
        # 返回完全相同的 JSON。
        rng = random.Random(20260917)
        shuffled_ok = True
        shuffled_detail = ""
        for _ in range(10):
            shuffled = existing[:]
            rng.shuffle(shuffled)
            resp = client.post(
                RECOMMEND_ENDPOINT,
                json=recommend_body(shuffled, "A", expected_xyz,
                                   max_row=sizes[0], max_col=sizes[1],
                                   max_tier=sizes[2]),
                timeout=TIMEOUT,
            )
            if resp.status_code != 200 or resp.json() != want:
                shuffled_ok = False
                shuffled_detail = f"{resp.status_code} {resp.text} != {want}"
                break
        check("现存箱乱序输入返回完全相同的 JSON", shuffled_ok, shuffled_detail)

        # 最简单的并列场景：期望点被占，同层六个邻居全部安全 -> 字典序首位。
        tie_body = recommend_body(
            [box(3, 3, 2, "A")], "D", (3, 3, 2),
            max_row=8, max_col=8, max_tier=5,
        )
        expect_recommended(
            client, "最近层全部并列 -> (2,3,2) 距离 1",
            tie_body,
            {"status": "recommended",
             "slot": {"row": 2, "col": 3, "tier": 2},
             "search_distance": 1},
        )

        print("\n[7] recommend-slot：随机布局批量核对字典序首位与乱序不变")
        layout_rng = random.Random(20260917)
        categories = ["A", "B", "C", "D"]
        random_ok = True
        random_detail = ""
        for _ in range(30):
            r_sz, c_sz, t_sz = (layout_rng.randint(1, 6),
                                layout_rng.randint(1, 6),
                                layout_rng.randint(1, 4))
            occupied: set[tuple[int, int, int]] = set()
            placed: list[dict[str, Any]] = []
            for _ in range(layout_rng.randint(0, r_sz * c_sz * t_sz)):
                pos = (layout_rng.randint(1, r_sz),
                       layout_rng.randint(1, c_sz),
                       layout_rng.randint(1, t_sz))
                if pos in occupied:
                    continue
                occupied.add(pos)
                placed.append(box(*pos, layout_rng.choice(categories)))
            exp = (layout_rng.randint(1, r_sz),
                   layout_rng.randint(1, c_sz),
                   layout_rng.randint(1, t_sz))
            cargo = layout_rng.choice(categories)
            expect = brute_force_slot((r_sz, c_sz, t_sz), placed, cargo, exp)
            body = recommend_body(placed, cargo, exp,
                                  max_row=r_sz, max_col=c_sz, max_tier=t_sz)
            resp = client.post(RECOMMEND_ENDPOINT, json=body, timeout=TIMEOUT)
            if resp.status_code != 200 or resp.json() != expect:
                random_ok = False
                random_detail = f"{resp.status_code} {resp.text} != {expect} body={body}"
                break
            shuffled_boxes = placed[:]
            layout_rng.shuffle(shuffled_boxes)
            resp = client.post(
                RECOMMEND_ENDPOINT,
                json=recommend_body(shuffled_boxes, cargo, exp,
                                   max_row=r_sz, max_col=c_sz, max_tier=t_sz),
                timeout=TIMEOUT,
            )
            if resp.json() != expect:
                random_ok = False
                random_detail = f"乱序后 {resp.text} != {expect}"
                break
        check("30 组随机布局与独立复算一致且乱序不变", random_ok, random_detail)

        print("\n[8] recommend-slot：小舱无安全位置 -> no_safe_slot 且无坐标")
        no_slot_cases = [
            # 名称, 现存箱, 待装类别, 期望点, 舱段尺寸
            ("1x1x1 唯一格被占",
             [box(1, 1, 1, "A")], "B", (1, 1, 1), (1, 1, 1)),
            ("2x1x1 满舱",
             [box(1, 1, 1, "A"), box(2, 1, 1, "C")], "B", (1, 1, 1), (2, 1, 1)),
            ("3x1x1 隔离规则封死两端（A-B 要求 3）",
             [box(2, 1, 1, "B")], "A", (2, 1, 1), (3, 1, 1)),
        ]
        for name, placed, cargo, exp, dims in no_slot_cases:
            body = recommend_body(placed, cargo, exp,
                                  max_row=dims[0], max_col=dims[1], max_tier=dims[2])
            response = client.post(RECOMMEND_ENDPOINT, json=body, timeout=TIMEOUT)
            ok = response.status_code == 200 and response.json() == {"status": "no_safe_slot"}
            check(f"{name} [仅回 no_safe_slot，不含坐标/距离]", ok,
                  f"{response.status_code} {response.text}")

        print("\n[9] recommend-slot：非法请求整份 422")
        good_recommend = recommend_body([], "A", (5, 5, 2))
        expect_recommend_422(client, "待装类别未知 E",
                             {**good_recommend, "cargo_category": "E"})
        expect_recommend_422(client, "待装类别为整数",
                             {**good_recommend, "cargo_category": 1})
        expect_recommend_422(client, "期望排为字符串",
                             {**good_recommend, "expected_row": "3"})
        expect_recommend_422(client, "期望排越上界",
                             {**good_recommend, "expected_row": 21})
        expect_recommend_422(client, "期望层越下界 0",
                             recommend_body([], "A", (5, 5, 0)))
        expect_recommend_422(client, "期望列越上界",
                             recommend_body([], "A", (5, 21, 2)))
        expect_recommend_422(client, "现存箱越界",
                             recommend_body([box(99, 1, 1, "A")], "A", (1, 1, 1)))
        expect_recommend_422(client, "重复占位",
                             recommend_body([box(1, 1, 1, "A"),
                                             box(1, 1, 1, "B")], "A", (1, 1, 1)))
        expect_recommend_422(client, "现存箱未知类别",
                             recommend_body([box(1, 1, 1, "X")], "A", (1, 1, 1)))
        expect_recommend_422(client, "缺 cargo_category 字段",
                             {k: v for k, v in good_recommend.items()
                              if k != "cargo_category"})
        expect_recommend_422(client, "缺 expected_tier 字段",
                             {k: v for k, v in good_recommend.items()
                              if k != "expected_tier"})
        expect_recommend_422(client, "顶层多余字段",
                             {**good_recommend, "ship": "x"})
        expect_recommend_422(client, "尺寸为浮点",
                             {**good_recommend, "max_row": 1.5})
        expect_recommend_422(client, "多问题并存仍只回一次 422",
                             recommend_body(
                                 [box(99, 1, 1, "A"), box(1, 1, 1, "E"),
                                  box(1, 1, 1, "A"), box(1, 1, 1, "B")],
                                 "A", (21, 1, 1)))

    print(f"\n共执行 {checks_run} 项检查。")
    if failures:
        print(f"验收失败：{len(failures)} 项未通过。")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("验收通过：全部临界距离合规，低一格冲突唯一且可复算，非法输入整份 422，"
          "寻位字典序首位与乱序不变性经独立复算核对，小舱无解仅回无坐标状态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
