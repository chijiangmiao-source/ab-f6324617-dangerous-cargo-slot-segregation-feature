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
* 无冲突时返回比较对数 C(n, 2)。

环境变量：
    VERIFY_BASE_URL  被测服务地址，默认 http://api:8000（Compose 网络）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("VERIFY_BASE_URL", "http://api:8000").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/v1/adjudicate"
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

    print(f"\n共执行 {checks_run} 项检查。")
    if failures:
        print(f"验收失败：{len(failures)} 项未通过。")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("验收通过：全部临界距离合规，低一格冲突唯一且可复算，非法输入整份 422。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
