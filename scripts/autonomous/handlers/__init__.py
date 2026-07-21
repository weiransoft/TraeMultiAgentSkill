"""Phase 18: 阶段 Handler - 暴露公共 API（v2.8 新增 ReviewHandler）。"""
from autonomous.handlers.base import StageHandler, StageResult
from autonomous.handlers.plan_handler import PlanHandler
from autonomous.handlers.dev_handler import DevHandler
from autonomous.handlers.verify_handler import VerifyHandler
from autonomous.handlers.fix_handler import FixHandler
from autonomous.handlers.review_handler import ReviewHandler

__all__ = [
    "StageHandler",
    "StageResult",
    "PlanHandler",
    "DevHandler",
    "VerifyHandler",
    "FixHandler",
    "ReviewHandler",
]
