"""请求/响应模型与整份输入校验。

校验原则：任何一项不合法（尺寸非正、坐标越界、重复箱位、未知类别、
类型错误）都由 Pydantic 在进入裁决逻辑之前一次性拒绝（HTTP 422），
不存在“部分裁决”。
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Category = Literal["A", "B", "C", "D"]

# 坐标从 1 开始；上界需要结合舱段尺寸做跨字段校验，因此这里只约束下界。
# StrictInt：拒绝数字字符串、浮点与布尔，坐标必须是 JSON 整数。
PositiveCoord = Annotated[StrictInt, Field(ge=1)]
PositiveSize = Annotated[StrictInt, Field(ge=1)]


class Container(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: PositiveCoord
    col: PositiveCoord
    tier: PositiveCoord
    category: Category


class AdjudicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_row: PositiveSize
    max_col: PositiveSize
    max_tier: PositiveSize
    containers: list[Container]

    @model_validator(mode="after")
    def _validate_layout(self) -> "AdjudicationRequest":
        problems: list[str] = []
        seen: set[tuple[int, int, int]] = set()
        duplicates: list[tuple[int, int, int]] = []

        for index, container in enumerate(self.containers, start=1):
            if (
                container.row > self.max_row
                or container.col > self.max_col
                or container.tier > self.max_tier
            ):
                problems.append(
                    f"第 {index} 个箱位 ({container.row},{container.col},"
                    f"{container.tier}) 超出舱段容量 "
                    f"(排<={self.max_row}, 列<={self.max_col}, 层<={self.max_tier})"
                )

            position = (container.row, container.col, container.tier)
            if position in seen and position not in duplicates:
                duplicates.append(position)
            seen.add(position)

        for row, col, tier in duplicates:
            problems.append(f"重复箱位: ({row},{col},{tier})")

        if problems:
            # 聚合成一次拒绝：整份请求 422，不返回任何裁决结果。
            raise ValueError("; ".join(problems))
        return self


class ConflictEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_a: Container
    container_b: Container
    actual_distance: int
    required_distance: int


class ConflictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["conflict"]
    first_conflict: ConflictEvidence


class CompliantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["compliant"]
    pairs_compared: int


AdjudicationResponse = Annotated[
    Union[ConflictResponse, CompliantResponse],
    Field(discriminator="status"),
]
