"""SU 全局限速器与预算计数器（架构 ARCH-SU-001 §2.3.13）。

- RateLimiter：相邻动作（跨 bucket 全局）间隔 ≥ delay_ms，time.monotonic 实现，
  对齐 NFR-SU-001"任何循环不得绕过"——所有采集循环（crawler/inspector）
  的每一次对外请求前必须先 wait()。
- BudgetTracker：pages/depth/actions/time 四维预算，任一耗尽即 exhausted=True
  并记录耗尽维度名（frontier 报告与第 10 节数据来源，REQ-SU-008）。
"""

import time
from typing import Optional

from su.config import RunBudget
from su.dto import BudgetSummaryRedacted

__all__ = ["RateLimiter", "BudgetTracker"]


class RateLimiter:
    """全局限速器（NFR-SU-001：等效 QPS ≤ 1/delay，任何循环不得绕过）。

    实现口径：单一全局时间戳（跨 bucket），time.monotonic 单调时钟
    （不受系统时间回拨影响）；wait() 计算"距上次动作已流逝的毫秒数"，
    不足 delay_ms 则 sleep 补足，保证相邻对外动作间隔 ≥ delay_ms。
    """

    def __init__(self, delay_ms: int) -> None:
        """初始化限速器。

        Args:
            delay_ms: 相邻动作最小间隔毫秒数（默认配置 1500ms，REQ-SU-008.2）。
                      ≤0 视为不限速（间隔 0）。
        """
        self._delay_ms = max(0, int(delay_ms))
        # 全局唯一时间戳：任何 bucket 的动作都会刷新它（跨 bucket 全局限速）
        self._last_action_ts = 0.0

    def wait(self, bucket: str = "default") -> None:
        """阻塞至距上次动作 ≥ delay_ms 后返回（跨 bucket 全局生效）。

        Args:
            bucket: 逻辑调用方标识（默认 "default"）。当前实现为**全局**间隔，
                bucket 仅作日志/扩展语义保留，不影响限速计算——这是设计意图：
                多透镜共享同一条对外流量阀门。
        """
        now = time.monotonic()
        elapsed_ms = (now - self._last_action_ts) * 1000.0
        if elapsed_ms < self._delay_ms:
            # 补足差额（秒）；首次调用 elapsed 巨大，直接放行
            time.sleep((self._delay_ms - elapsed_ms) / 1000.0)
        # 动作完成后刷新全局时间戳（以"等待结束时刻"为基准，串行化间隔）
        self._last_action_ts = time.monotonic()


class BudgetTracker:
    """四维预算计数器（REQ-SU-008：pages/depth/actions/time）。

    状态语义：
      - pages/actions 为"已消耗量"，consume_*() 在动作**发生前**调用，
        返回 False 表示预算不足、动作不得发生（调用方转入 frontier 记录）；
      - depth 为只读判定（depth_allowed），不消耗；
      - time 以构造时刻起算（time_budget_minutes），墙钟自然流逝；
      - 任一维度耗尽 → exhausted 属性返回该维度名（首达维度），
        summary() 供第 1 节 + summary.json 输出。
    """

    # 四维耗尽维度名（报告/exit_reason 前缀 'budget_exhausted:<维度>' 对齐 §3 DDL）
    DIM_PAGES = "pages"
    DIM_ACTIONS = "actions"
    DIM_DEPTH = "depth"
    DIM_TIME = "time"

    def __init__(self, budget: RunBudget) -> None:
        """初始化预算计数器。

        Args:
            budget: 已校验的 RunBudget（四维上限全部正整数，validate 保证）。
        """
        self._budget = budget
        self._pages_consumed = 0          # 已完成页面数
        self._actions_consumed = 0        # 已执行动作数（全站累计）
        self._depth_reached = 0           # 已触达最大深度（观测值）
        self._started_monotonic = time.monotonic()   # 墙钟起点（单调时钟）
        self._exhausted_dimension: Optional[str] = None  # 首个耗尽维度（定格不覆盖）

    # ---- 消耗判定（调用方在动作发生前调用；False=预算不足应停止）----

    def consume_page(self) -> bool:
        """尝试消耗一个页面预算。

        Returns:
            bool: True=允许探索下一页（计数 +1）；False=pages 预算耗尽
                （计数不变，调用方应停止 BFS 并把队列剩余节点记为 frontier）。
        """
        if self._pages_consumed >= self._budget.max_pages:
            # 定格首个耗尽维度（后续其他维度也耗尽不覆盖，保证报告稳定）
            if self._exhausted_dimension is None:
                self._exhausted_dimension = self.DIM_PAGES
            return False
        self._pages_consumed += 1
        return True

    def consume_action(self) -> bool:
        """尝试消耗一个动作预算（单页动作数上限，REQ-SU-008.1）。

        实现口径：max_actions_per_page 是"每页"上限——crawler 在**每页开始时**
        调用 reset_page_actions() 归零页内计数，本方法页内累计。

        Returns:
            bool: True=允许（页内计数 +1）；False=本页动作预算耗尽。
        """
        if self._page_actions >= self._budget.max_actions_per_page:
            if self._exhausted_dimension is None:
                self._exhausted_dimension = self.DIM_ACTIONS
            return False
        self._page_actions += 1
        self._actions_consumed += 1
        return True

    def reset_page_actions(self) -> None:
        """页边界重置页内动作计数（crawler 每开始一页调用一次）。

        注意：全局累计计数 _actions_consumed 不归零（summary 报告全站动作总量）。
        """
        self._page_actions = 0

    # 页内动作计数初始化（供 __init__ 后立即拥有该属性，避免属性未定义）
    _page_actions = 0

    def depth_allowed(self, depth: int) -> bool:
        """判定目标深度是否允许探索（只读判定，不消耗）。

        Args:
            depth: 候选节点的 BFS 深度。

        Returns:
            bool: True=depth ≤ max_depth；False=超深（调用方不展开该节点，
                但**不**把 depth 记为 exhausted——超深节点按"按规则跳过"处理，
                区别于预算耗尽语义，REQ-SU-008 AC3 的 frontier 报告各自标注）。
        """
        # 观测最大深度只增不减（无论是否放行都记录，第 1 节报告用）
        if depth > self._depth_reached:
            self._depth_reached = depth
        return depth <= self._budget.max_depth

    # ---- 状态查询 ----

    def check_time(self) -> bool:
        """检查时间预算是否仍有余量（crawler BFS 循环每轮调用）。

        墙钟耗尽时定格 exhausted=time（供 frontier 报告标注）。

        Returns:
            bool: True=仍在时间预算内；False=已超出 time_budget_minutes。
        """
        if self.elapsed_seconds() >= self._budget.time_budget_minutes * 60.0:
            if self._exhausted_dimension is None:
                self._exhausted_dimension = self.DIM_TIME
            return False
        return True

    @property
    def exhausted(self) -> Optional[str]:
        """返回首个耗尽维度名（pages/actions/time），未耗尽返回 None。

        说明：depth 超限是"节点级跳过规则"而非全局停止条件，按设计不计入
        exhausted（§2.3.13 注释：任一耗尽即停的是 pages/actions/time 三维，
        depth 由 depth_allowed 逐节点判定）。
        """
        # 属性惰性同步 time 维度：即使调用方忘了 check_time，
        # 读取 exhausted 时也补一次墙钟判定，杜绝超时漏检
        self.check_time()
        return self._exhausted_dimension

    def elapsed_seconds(self) -> float:
        """返回自构造以来的墙钟流逝秒数（单调时钟，不受系统时间调整影响）。"""
        return time.monotonic() - self._started_monotonic

    def summary(self) -> BudgetSummaryRedacted:
        """生成预算消耗摘要（UNDERSTANDING.md 第 1 节 + summary.json 数据源）。

        Returns:
            BudgetSummaryRedacted: 含四维配置上限、实际消耗、耗尽维度名。
            摘要全部为运行参数与计数，无外部数据，天然无敏感值。
        """
        return BudgetSummaryRedacted(
            max_pages=self._budget.max_pages,
            max_depth=self._budget.max_depth,
            max_actions_per_page=self._budget.max_actions_per_page,
            time_budget_minutes=self._budget.time_budget_minutes,
            pages_consumed=self._pages_consumed,
            actions_consumed=self._actions_consumed,
            depth_reached=self._depth_reached,
            elapsed_seconds=round(self.elapsed_seconds(), 3),
            exhausted_dimension=self.exhausted,
        )
