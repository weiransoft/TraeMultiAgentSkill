"""DispatchResult 数据类（H-7 修复：替代 bool | None）。

字段语义：
- matched_plugin: 匹配的 plugin name（None = 无匹配）
- success: 执行是否成功（True/False）
- error: 执行异常（None = 无异常；error 不为 None 时 success 必为 False）
- skipped_reason: 跳过原因（None / "no_match" / "dry_run" / "mutex_violation"）

__bool__ 兼容：bool(result) 等价于 (matched and success)，
保留旧 bool() 调用方（Phase 14 前的 facade 内部代码）正常工作。
"""
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class DispatchResult:
    """dispatch() 结构化返回（H-7 修复）。"""

    matched_plugin: Optional[str] = None
    success: bool = False
    error: Optional[BaseException] = None
    skipped_reason: Optional[str] = None
    data: Optional[Any] = None

    def __bool__(self) -> bool:
        """兼容旧 bool() 调用：success 字段为权威判定（matched_and_success / dry_run 都算 True）。"""
        return bool(self.success)


__all__ = ["DispatchResult"]
