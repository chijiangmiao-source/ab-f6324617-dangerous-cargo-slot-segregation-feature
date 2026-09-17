# 舱段危险品箱隔离裁决服务

纯后端 FastAPI 服务：接收舱段容量与若干危险品箱位，按固定隔离规则表逐对裁决，
返回**首个冲突**的完整证据；无冲突则返回合规及实际比较对数。基于
Python 3.13 + FastAPI + Pydantic v2，规则与接口全量 pytest 覆盖，
提供 Docker Compose 与名为 `verify` 的一次性验收服务。

## 目录结构

```
.
├── app/                     # 应用代码（纯 Python，无其他框架代码）
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口（/healthz、/api/v1/adjudicate）
│   ├── models.py            # 请求/响应模型 + 整份输入校验（422）
│   └── rules.py             # 规则表、曼哈顿距离、裁决与首冲突排序（纯函数）
├── tests/                   # 67 项 pytest 单元/接口测试
│   ├── test_rules.py
│   └── test_api.py
├── scripts/
│   ├── healthcheck.py       # 容器 HEALTHCHECK 探针脚本（200+status=ok -> 0）
│   └── verify.py            # 一次性验收脚本（verify 服务入口）
├── Dockerfile               # python:3.13-slim
├── docker-compose.yml       # api（常驻）+ verify（一次性）
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## 快速开始

### Docker Compose（推荐，使用 Python 3.13 镜像）

```bash
# 默认启动整套服务：只启动常驻 API（verify 在独立 profile 中，不会运行）。
# 宿主端口默认 8000，可用 API_PORT 覆盖。
docker compose up -d --build

# 一次性验收：构建（若尚未构建）-> 后台拉起 api 并等其健康 ->
# 前台跑全部验收用例 -> verify 容器退出（退出码即结论）并自动删除。
docker compose --profile verify run --build --rm verify; echo "exit=$?"
# exit=0 表示全部通过。
```

为什么验收服务不放在默认启动集合：`verify` 是**一次性**容器（跑完即退出）。
若与常驻 API 一同 `docker compose up`，它正常退出（exit 0）会导致前台
`up` 命令报错并收掉整套服务。因此 `verify` 归入独立的 `verify` profile，
默认启动只有持续可用的 API；显式加 `--profile verify` 才运行验收。

`API_PORT` 只覆盖**宿主侧**映射端口，容器内始终监听 8000：

```bash
API_PORT=9090 docker compose up -d
# 访问 http://localhost:9090
```

停止：

```bash
docker compose down
```

### 本地运行（需要 Python 3.13；其他 3.11+ 版本也能跑，但 Docker 用 3.13）

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 另开终端对运行中的服务验收：
VERIFY_BASE_URL=http://127.0.0.1:8000 python scripts/verify.py
```

## 接口说明（规则与边界例子就在这里）

### 健康检查地址

以下三个地址均返回服务健康信息（直接访问不会 404）：

| 地址 | 返回 |
|---|---|
| `GET /healthz` | `200 {"status": "ok"}` — Docker HEALTHCHECK 探针固定地址 |
| `GET /health` | `200 {"status": "ok", "service": ..., "version": ...}` |
| `GET /` | `200` 健康信息 + 可用端点与 `/docs` 入口 |

容器健康检查由 `scripts/healthcheck.py` 执行：请求 `/healthz`，HTTP 200 且
JSON 中 `status == "ok"` 才以退出码 0 报告健康；连接失败、超时、非 200 或
状态异常一律退出码 1（不依赖第三方库，slim 镜像可直接运行）。

### `POST /api/v1/adjudicate`

#### 请求体

| 字段 | 类型 | 约束 |
|---|---|---|
| `max_row` | int | 舱段最大排，**必须 ≥ 1** |
| `max_col` | int | 舱段最大列，**必须 ≥ 1** |
| `max_tier` | int | 舱段最大层，**必须 ≥ 1** |
| `containers` | array | 箱位列表，可为空 |
| `containers[].row` | int | 排号，**从 1 开始**，`1 ≤ row ≤ max_row` |
| `containers[].col` | int | 列号，**从 1 开始**，`1 ≤ col ≤ max_col` |
| `containers[].tier` | int | 层号，**从 1 开始**，`1 ≤ tier ≤ max_tier` |
| `containers[].category` | string | **仅允许 `"A"` `"B"` `"C"` `"D"`**（大小写敏感） |

所有坐标为整数；额外字段、数字字符串、布尔、浮点都会被拒绝。
每个箱位必须唯一（`row/col/tier` 三元组不可重复）。

#### 规则表（固定，不可配置）

两箱之间要求的最小距离：

| 类别对（无序，对称适用） | 要求距离 |
|---|---|
| **A–B** | ≥ 3 |
| **A–C** | ≥ 2 |
| **B–D** | ≥ 2 |
| 其余任意组合（含同类，如 A–A、B–B） | ≥ 1 |

#### 距离定义（关键，勿误解）

```
实际距离 = |排差| + |列差| + |层差|     （三轴曼哈顿距离，L1）
```

* **不是欧氏距离**：例如三轴各差 1，距离 = 1+1+1 = **3**（而非 √3）。
* **不忽略中间空位**：`(1,1,1)` 与 `(1,4,1)` 距离恒为 3，无论 `(1,2,1)`、
  `(1,3,1)` 有没有箱子。空位不会把两端“折叠”靠近。
* 因箱位唯一，两不同箱位的最小距离就是 1，所以要求 ≥ 1 的组合（同类等）
  天然不可能冲突。

#### 判定与“首个冲突”排序

* `实际距离 < 要求距离` → **冲突**；`实际距离 == 要求距离` → **合规**
  （临界相等不算冲突）。
* 服务比较全部 C(n,2) 个无序箱对；若存在多个冲突，返回的首项**唯一**且与输入
  顺序无关。先把每对内两箱按坐标三元组 `(row, col, tier)` 字典序分为
  “较小箱位 / 较大箱位”，再按：
  1. **较小箱位**的 `(排, 列, 层)` 升序；
  2. 若较小箱位相同，则按**较大箱位**的 `(排, 列, 层)` 升序。

  **类别不参与排序。** `container_a` 恒为较小箱位，`container_b` 恒为较大箱位。

#### 响应

无冲突 → `200`：

```json
{ "status": "compliant", "pairs_compared": 6 }
```

有冲突 → `200`，只返回首项及双方完整坐标、实际距离、要求距离：

```json
{
  "status": "conflict",
  "first_conflict": {
    "container_a": { "row": 1, "col": 1, "tier": 1, "category": "A" },
    "container_b": { "row": 1, "col": 3, "tier": 1, "category": "B" },
    "actual_distance": 2,
    "required_distance": 3
  }
}
```

（上例：A–B 要求 3，纯列差 2 < 3，冲突。）

自动生成的交互文档：`GET /docs`（Swagger UI）、`GET /openapi.json`。

### 边界例子（与规则同节阅读）

| 场景 | 箱位 | 类别对 | 实际 | 要求 | 结论 |
|---|---|---|---|---|---|
| 临界合规（纯列差，中间全空位） | `(1,1,1)`/`(1,4,1)` | A/B | 3 | 3 | ✅ 合规 |
| 临界合规（三轴各差 1，非欧氏） | `(1,1,1)`/`(2,2,2)` | A/B | 3 | 3 | ✅ 合规 |
| 临界合规（层差/排差叠加，看似分散） | `(3,5,1)`/`(4,5,3)` | A/B | 3 | 3 | ✅ 合规 |
| **低一格**即冲突 | `(1,1,1)`/`(1,3,1)` | A/B | 2 | 3 | ❌ 冲突 |
| 临界合规（纯层差） | `(3,3,2)`/`(3,3,4)` | A/C | 2 | 2 | ✅ 合规 |
| **低一格**即冲突（仅隔一层） | `(3,3,2)`/`(3,3,3)` | A/C | 1 | 2 | ❌ 冲突 |
| 临界合规（纯排差） | `(4,1,1)`/`(6,1,1)` | B/D | 2 | 2 | ✅ 合规 |
| **低一格**即冲突（相邻排） | `(4,1,1)`/`(5,1,1)` | B/D | 1 | 2 | ❌ 冲突 |
| 同类相邻 | `(1,1,1)`/`(1,1,2)` | A/A | 1 | 1 | ✅ 合规 |
| 其余异类相邻 | `(1,1,1)`/`(2,1,1)` | A/D | 1 | 1 | ✅ 合规 |
| 坐标恰好取到容量上界 | max 为 `(3,4,2)`，箱位 `(3,4,2)` | A/B | — | — | ✅ 受理 |
| 空舱 | `containers: []` | — | — | — | ✅ `pairs_compared=0` |

手动请求示例：

```bash
curl -s http://localhost:8000/api/v1/adjudicate \
  -H 'Content-Type: application/json' \
  -d '{"max_row":10,"max_col":10,"max_tier":5,
       "containers":[{"row":1,"col":1,"tier":1,"category":"A"},
                     {"row":1,"col":3,"tier":1,"category":"B"}]}'
```

### 错误处理：整份 422，无部分裁决

以下任一情况，**整份请求**返回 `422 Unprocessable Entity`（FastAPI
标准 `{"detail": [...]}` 结构），绝不返回部分裁决，也不会夹带
`status` / `first_conflict` / `pairs_compared` 字段：

* 任一尺寸非正（`max_row/max_col/max_tier < 1`）、缺失或类型不是整数；
* 任一坐标越界（`< 1` 或 `> max_*`，含恰好超过上界 1 格）；
* 重复箱位；
* 未知类别（非 A/B/C/D，含小写 `"a"`、`"E"`）；
* 缺少必填字段，或请求体/箱位中出现未声明字段；
* 坐标/尺寸不是整数（数字字符串、`true`、`1.0` 同样拒绝）。

多个问题同时存在时仍只回一次 422，错误明细聚合在 `detail` 中。

## 验收服务 `verify`

`scripts/verify.py` 是**一次性**脚本，也是 Compose 中的 `verify` 服务：

1. 等待 `api` 健康检查通过后启动；
2. 执行 100+ 项固定检查：三类特殊规则的**临界距离稳定判合规**、
   低一格组合产生**唯一冲突**、冲突证据由脚本侧**独立按曼哈顿距离复算**、
   多冲突首项排序及输入乱序不变性、比较对数、以及全部非法输入的整份 422；
3. 打印每项 PASS/FAIL，全部通过退出码 `0`，否则 `1`，随后容器退出
   （`restart: "no"`，不会常驻或假成功）。

```bash
docker compose --profile verify run --build --rm verify
```

本地直跑：`VERIFY_BASE_URL=http://127.0.0.1:8000 python scripts/verify.py`。
首项检查即三个健康地址与 `scripts/healthcheck.py` 探针退出码。

## 测试

```bash
pytest -q
```

覆盖（73 项）：规则矩阵完整性与对称性、曼哈顿三轴定义、非欧氏判定、
空位不折叠、临界相等合规、低一格冲突、首冲突双层排序、类别不参与排序、
20 次乱序可复现性、比较对数计数、三个健康地址与探针脚本退出码，以及全部
422 边界（尺寸非正、上下界越界、重复、未知类别、缺字段、多字段、类型错误、
空体）。

## 设计说明

* `app/rules.py` 与 Web 层完全解耦，是无副作用的纯函数模块，可单独复用；
  规则表用无序类别对（`frozenset`）查表，对称适用且不存在未实现分支。
* 校验在 Pydantic 模型层（含跨字段的上界与重复检测，聚合后一次拒绝），
  进入裁决逻辑的数据一定合法，故裁决路径无需再处理非法输入。
* 无固定结果、无假接口、无占位分支：所有分支均有测试覆盖。
