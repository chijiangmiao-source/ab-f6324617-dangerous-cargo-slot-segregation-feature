"""请求/响应模型与整份输入校验。

校验原则：任何一项不合法（尺寸非正、坐标越界、重复箱位、未知类别、
类型错误）都由 Pydantic 在进入业务逻辑之前一次性拒绝（HTTP 422），
不存在“部分裁决/部分推荐”。
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Category = Literal["A", "B", "C", "D"]

# 坐标从 1 开始；上界需要结合舱段尺寸做跨字段校验，因此这里只约束下界。
# StrictInt：拒绝数字字符串、浮点与布尔，坐标必须是 JSON 整数。
PositiveCoord = Annotated[StrictInt, Field(ge=1)]
PositiveSize = Annotated[StrictInt, Field(ge=1)]
# 搜索距离是壳层编号（期望点本身为第 0 层），非负整数。
NonNegativeDistance = Annotated[StrictInt, Field(ge=0)]


class Container(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: PositiveCoord
    col: PositiveCoord
    tier: PositiveCoord
    category: Category


class LayoutRequest(BaseModel):
    """两类请求共用的“舱段尺寸 + 现存箱位”结构与整份校验。"""

    model_config = ConfigDict(extra="forbid")

    max_row: PositiveSize
    max_col: PositiveSize
    max_tier: PositiveSize
    containers: list[Container]

    @model_validator(mode="after")
    def _validate_layout(self) -> "LayoutRequest":
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

        # 子类钩子：期望坐标上界等跨字段问题也聚合进同一次拒绝。
        self._collect_extra_problems(problems)

        if problems:
            # 聚合成一次拒绝：整份请求 422，不返回任何业务结果。
            raise ValueError("; ".join(problems))
        return self

    def _collect_extra_problems(self, problems: list[str]) -> None:
        """子类追加跨字段校验问题；基类无额外约束。"""


class AdjudicationRequest(LayoutRequest):
    """整舱裁决请求：仅舱段尺寸与现存箱位。"""


class RecommendSlotRequest(LayoutRequest):
    """寻位请求：在现存箱位之外，另含待装箱类别与期望坐标。"""

    cargo_category: Category
    expected_row: PositiveCoord
    expected_col: PositiveCoord
    expected_tier: PositiveCoord

    def _collect_extra_problems(self, problems: list[str]) -> None:
        # 下界（>=1）由 PositiveCoord 保证；期望坐标同样必须在舱内。
        if self.expected_row > self.max_row:
            problems.append(
                f"期望排 {self.expected_row} 超出舱段容量 (排<={self.max_row})"
            )
        if self.expected_col > self.max_col:
            problems.append(
                f"期望列 {self.expected_col} 超出舱段容量 (列<={self.max_col})"
            )
        if self.expected_tier > self.max_tier:
            problems.append(
                f"期望层 {self.expected_tier} 超出舱段容量 (层<={self.max_tier})"
            )


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


class Slot(BaseModel):
    """推荐箱位：只含坐标，类别即请求中的待装类别，不重复回显。"""

    model_config = ConfigDict(extra="forbid")

    row: PositiveCoord
    col: PositiveCoord
    tier: PositiveCoord


class RecommendedSlotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["recommended"]
    slot: Slot
    search_distance: NonNegativeDistance


class NoSafeSlotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 无解时只回状态：不带坐标，也不带搜索距离。
    status: Literal["no_safe_slot"]


RecommendSlotResponse = Annotated[
    Union[RecommendedSlotResponse, NoSafeSlotResponse],
    Field(discriminator="status"),
]
