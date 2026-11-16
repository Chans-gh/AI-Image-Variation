"""重试与预算控制（SPEC 01 §9.7、D-25）。

预算相关的所有数值来自配置；本模块只做判定与记账，不发起请求。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from shared.errors import IVError

from ..config import load_config


@dataclass
class RetryPolicy:
    max_extra_retries: int
    retry_on: frozenset[str]
    never_retry_on: frozenset[str]
    backoff_base_sec: float
    backoff_max_sec: float
    jitter_ratio: float

    @classmethod
    def from_config(cls, config: dict | None = None, *, unit: str = "frame",
                    max_extra_retries: int | None = None) -> "RetryPolicy":
        config = config or load_config()
        retry = config["retry"]
        if max_extra_retries is None:
            max_extra = (retry["grid_max_attempts"] - 1 if unit == "grid"
                         else retry["frame_max_extra_retries"])
        else:
            max_extra = int(max_extra_retries)
        return cls(
            max_extra_retries=max_extra,
            retry_on=frozenset(retry["retry_on"]),
            never_retry_on=frozenset(retry["never_retry_on"]),
            backoff_base_sec=float(retry["backoff_base_sec"]),
            backoff_max_sec=float(retry["backoff_max_sec"]),
            jitter_ratio=float(retry["jitter_ratio"]),
        )

    def should_retry(self, code: str, attempt: int) -> bool:
        """attempt 为已发生的尝试次数（1 表示首试已失败）。"""
        if code in self.never_retry_on:
            return False
        if code not in self.retry_on:
            return False
        return attempt <= self.max_extra_retries

    def backoff_seconds(self, attempt: int, retry_after: float | None = None,
                        rng: random.Random | None = None) -> float:
        if retry_after is not None:
            return max(0.0, float(retry_after))
        rng = rng or random.Random()
        delay = min(self.backoff_base_sec * (2 ** max(0, attempt - 1)), self.backoff_max_sec)
        jitter = delay * self.jitter_ratio
        return max(0.0, delay + rng.uniform(-jitter, jitter))


@dataclass
class Budget:
    """排产预算与记账。"""

    limit: int | None
    planned: int
    cost_per_call: dict[str, float] = field(default_factory=dict)
    currency: str = "USD"
    calls_used: int = 0
    cost_used: float = 0.0
    degradations: list[str] = field(default_factory=list)

    @classmethod
    def plan(cls, *, output_mode: str, image_count: int, input_type: str,
             tier: str, config: dict | None = None) -> "Budget":
        config = config or load_config()
        budget_cfg = config["budget"]
        retry = config["retry"]
        if output_mode == "grid":
            planned = 1 + (retry["grid_max_attempts"] - 1)
        else:
            planned = image_count + retry["frame_max_extra_retries"]
        return cls(
            limit=budget_cfg["default_call_budget_limit"],
            planned=planned,
            cost_per_call=dict(budget_cfg["cost_per_call"]),
            currency=budget_cfg["cost_currency"],
        )

    @property
    def cost_estimate(self) -> float:
        unit = self.cost_per_call.get("1K", 0.0)
        return round(self.planned * unit, 4)

    def preflight(self) -> None:
        """排产前检查：超限直接 BUDGET_EXCEEDED，不静默继续。"""
        if self.limit is not None and self.planned > self.limit:
            raise IVError("BUDGET_EXCEEDED",
                          f"计划调用 {self.planned} 次超过预算上限 {self.limit}，请拆批后重试",
                          planned=self.planned, limit=self.limit,
                          split_suggestion=split_suggestion(self.planned))

    def consume(self, *, tier: str, cost_hint: float | None = None) -> None:
        """每次真实调用记账；超限时抛出 BUDGET_EXCEEDED 并保留已完成结果。"""
        self.calls_used += 1
        unit = cost_hint if cost_hint is not None else self.cost_per_call.get(tier, 0.0)
        self.cost_used = round(self.cost_used + unit, 6)
        if self.limit is not None and self.calls_used > self.limit:
            raise IVError("BUDGET_EXCEEDED",
                          f"已调用 {self.calls_used} 次，超过预算上限 {self.limit}",
                          calls_used=self.calls_used, limit=self.limit)

    def to_execution(self) -> dict:
        return {
            "calls_planned": self.planned,
            "calls_used": self.calls_used,
            "call_budget_limit": self.limit,
            "budget_exceeded": self.limit is not None and self.calls_used > self.limit,
            "cost_estimate": self.cost_estimate,
            "cost_currency": self.currency,
            "degradations": list(self.degradations),
        }


def split_suggestion(total: int, per_batch: int | None = None,
                     config: dict | None = None) -> list[list[int]]:
    """拆批建议（Sequence 多批 / Grid 降规模）。"""
    config = config or load_config()
    per_batch = per_batch or config["budget"]["batch_default_max_items"]
    batches: list[list[int]] = []
    start = 1
    while start <= total:
        end = min(start + per_batch - 1, total)
        batches.append(list(range(start, end + 1)))
        start = end + 1
    return batches
