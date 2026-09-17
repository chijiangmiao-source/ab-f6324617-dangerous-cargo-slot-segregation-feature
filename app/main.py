"""FastAPI 入口。"""

from fastapi import FastAPI

from .models import (
    AdjudicationRequest,
    AdjudicationResponse,
    ConflictEvidence,
)
from .rules import adjudicate

SERVICE_NAME = "dangerous-goods-adjudicator"
SERVICE_VERSION = "1.0.0"

app = FastAPI(
    title="舱段危险品箱隔离裁决服务",
    version=SERVICE_VERSION,
    description="对同一舱段内的危险品箱位做强制隔离距离裁决（曼哈顿距离）。",
)


def _health_info() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


# 三个地址均返回服务健康信息：/healthz 为容器健康检查与探针的固定地址，
# /health 为通用别名，/ 便于直接访问根路径（均不返回 404）。
@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health", tags=["meta"])
def health_alias() -> dict[str, str]:
    return _health_info()


@app.get("/", tags=["meta"])
def root() -> dict[str, object]:
    info = _health_info()
    return {
        **info,
        "docs": "/docs",
        "endpoints": ["/healthz", "/health", "/api/v1/adjudicate"],
    }


@app.post(
    "/api/v1/adjudicate",
    response_model=AdjudicationResponse,
    tags=["adjudication"],
    summary="裁决一批箱位是否满足隔离规则",
)
def adjudicate_endpoint(
    request: AdjudicationRequest,
) -> dict[str, object]:
    result = adjudicate(request.containers)

    if result.first_conflict is None:
        return {"status": "compliant", "pairs_compared": result.pairs_compared}

    conflict = result.first_conflict
    return {
        "status": "conflict",
        "first_conflict": ConflictEvidence(
            container_a=conflict.smaller,
            container_b=conflict.larger,
            actual_distance=conflict.actual_distance,
            required_distance=conflict.required_distance,
        ),
    }
