"""V3 CLI 解析层：从 trae_agent_dispatch_v2.py line 72-318 完整搬迁 parse_arguments()。

约束：
- 保持所有 CLI flag 与 god module 100% 等价
- 保持 --task 非必填（Phase 15 B-3 修复）
- 保持 --dry-run 行为
- 风险-11：plugin 通过 `from plugins.X import X` 引用
  dispatcher 内部用 f"--{plugin.name}" 派生 CLI flag（B-4 修复）
- Phase 17：hot-reload 互斥 group + --hot-reload-dir 路径校验
  （P0-1 互斥 + P0-7 路径安全第一层防护）
"""

import argparse
from pathlib import Path


def _str_to_bool(value: str) -> bool:
    """将字符串形式的布尔值转换为 bool。

    支持：true / false / 1 / 0 / yes / no（大小写不敏感）。
    用于兼容旧版 CLI 中 `--flag true` 的传参风格。

    Args:
        value: 待转换的字符串。

    Returns:
        bool: 转换后的布尔值。

    Raises:
        argparse.ArgumentTypeError: 无法识别为布尔值时抛出。
    """
    lower = value.strip().lower()
    if lower in ("true", "1", "yes"):
        return True
    if lower in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"无效布尔值：{value}（期望 true/false）")


def _validate_drop_in_dir(value: str) -> str:
    """Phase 17 v3 P0-7 第一层：CLI 层早期校验 drop-in 目录路径。

    拒绝：
    - 绝对路径（任何操作系统均拒绝，强制相对路径）
    - 包含 '..' 的相对路径（粗略检查；watcher 还会 resolve() 二次校验）

    Args:
        value: CLI 传入的字符串路径

    Returns:
        通过校验的原始字符串（保持 str 类型，避免 Path 转换的兼容性问题）

    Raises:
        argparse.ArgumentTypeError: 路径不安全
    """
    p = Path(value)
    if p.is_absolute():
        raise argparse.ArgumentTypeError(
            f"--hot-reload-dir 必须为相对路径（绝对路径被拒绝）：{value}"
        )
    if ".." in p.parts:
        raise argparse.ArgumentTypeError(f"--hot-reload-dir 不能包含 '..'：{value}")
    return value


def parse_arguments():
    """解析 CLI 参数（保持与 god module 100% 等价）。

    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description="Trae Agent 调度脚本 v2.0 - 调度不同的智能体角色来实现任务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 修复 Phase 15 B-3：--task 改为非必需（--goal-graph / --goal-cancel 等
    # 只读 / 状态变更模式不需要 --task）
    parser.add_argument(
        "--task",
        type=str,
        required=False,
        default="",
        help='任务描述，例如："实现 SOUL-007 专注模式切换测试用例"',
    )

    parser.add_argument(
        "--agent",
        type=str,
        choices=[
            "architect",
            "product-manager",
            "tester",
            "solo-coder",
            "ui-designer",
            "devops",
            "auto",
        ],
        default="auto",
        help="指定要调度的智能体角色（默认：auto - 自动匹配）",
    )

    # 旧版 / Claude Code 触发器兼容性 flag：
    # --consensus / --explain / --match-strategy 在 autonomous dispatcher_adapter
    # 构造 Namespace 时使用；直接调用 trae_agent_dispatch.py 时需解析通过。
    parser.add_argument(
        "--consensus",
        type=_str_to_bool,
        default=False,
        help="多角色代码审查是否启用共识汇总（true/false；默认 false）",
    )
    parser.add_argument(
        "--explain",
        type=_str_to_bool,
        default=False,
        help="是否输出解释性分析（true/false；默认 false）",
    )
    parser.add_argument(
        "--match-strategy",
        type=str,
        default="auto",
        choices=["auto", "exact", "fuzzy", "semantic"],
        help="角色匹配策略（auto/exact/fuzzy/semantic；默认 auto）",
    )

    parser.add_argument(
        "--project-root", type=str, default=".", help="项目根目录路径（默认：当前目录）"
    )

    parser.add_argument("--task-file", type=str, help="任务文件路径")

    parser.add_argument("--output", type=str, default=None, help="输出文件路径（可选）")

    parser.add_argument("--verbose", action="store_true", help="启用详细输出模式")

    parser.add_argument(
        "--dry-run", action="store_true", help="仅模拟执行，不实际调用智能体"
    )

    parser.add_argument(
        "--use-v1", action="store_true", help="使用 v1.0 版本逻辑（不使用新组件）"
    )

    parser.add_argument(
        "--project-full-lifecycle",
        action="store_true",
        help="启用项目全生命周期模式（8 阶段标准工作流程：需求→架构→UI→测试→任务→开发→测试→发布）",
    )

    # Phase 11 新增：/loop + /goal 集成
    parser.add_argument(
        "--loop",
        type=int,
        default=1,
        help="循环执行次数（默认 1 = 不循环；范围 [1, 100]）",
    )

    parser.add_argument(
        "--goal",
        type=str,
        default=None,
        help="目标 ID（kebab-case，例如：fix-tests / refactor-auth）",
    )

    parser.add_argument(
        "--goal-desc",
        type=str,
        default=None,
        help="目标描述（创建新目标时必填；已存在目标可省略）",
    )

    parser.add_argument(
        "--criteria",
        action="append",
        default=[],
        help='验收标准（可多次传入，例如：--criteria "tests pass" --criteria "no warnings"）',
    )

    parser.add_argument(
        "--convergence-window",
        type=int,
        default=3,
        help="收敛窗口：连续 N 次无新产出则提前退出（默认 3）",
    )

    # Phase 13 新增：多 Goal 编排 CLI 标志
    parser.add_argument(
        "--multi-goal",
        type=str,
        default=None,
        help="以指定 root Goal ID 为入口执行多 Goal 编排（触发 DAG 调度器）",
    )
    parser.add_argument(
        "--goal-parent",
        type=str,
        default=None,
        help="创建新 Goal 时指定 parent_goal_id（多 Goal 树）",
    )
    parser.add_argument(
        "--goal-depends",
        action="append",
        default=[],
        help="为新 Goal 增加 depends_on 依赖（可多次传入，例如：--goal-depends g1 --goal-depends g2）",
    )
    parser.add_argument(
        "--goal-aggregation",
        type=str,
        default="AND",
        choices=["AND", "OR", "MAJORITY"],
        help="子 Goal 聚合策略（AND=全部成功 / OR=任一成功 / MAJORITY=多数成功；默认 AND）",
    )
    parser.add_argument(
        "--goal-resume",
        type=str,
        default=None,
        help="续跑指定 Goal（不带 --force 时仅续可续跑 goal）",
    )
    parser.add_argument(
        "--goal-resume-force",
        action="store_true",
        help="强制续跑（包括 ABANDONED 状态的 Goal / FAILED 续跑超限）",
    )
    parser.add_argument(
        "--goal-max-resume-count",
        type=int,
        default=3,
        help="覆盖单 Goal 续跑上限（默认 3）",
    )
    parser.add_argument(
        "--reuse-threshold",
        type=float,
        default=0.85,
        help="跨 Goal 复用相似度阈值（0.0-1.0；默认 0.85）",
    )
    parser.add_argument(
        "--disable-iteration-reuse",
        action="store_true",
        help="禁用跨 Goal iteration 语义复用",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=10,
        help="多 Goal 编排时 DAG 并发 worker 数（默认 10）",
    )
    parser.add_argument(
        "--goal-report",
        type=str,
        default=None,
        choices=["json", "md"],
        help="多 Goal 编排完成后输出报告（json / md）",
    )

    # Phase 14 新增：--goal-cancel <goal_id>
    parser.add_argument(
        "--goal-cancel",
        type=str,
        default=None,
        help="取消指定 root Goal 及其所有子 Goal（Phase 14 完善："
        "终止运行中子进程 + 标记 ABANDONED + 释放资源）",
    )

    # Phase 15 新增：DAG 依赖图可视化
    parser.add_argument(
        "--goal-graph",
        type=str,
        default=None,
        help="可视化指定 root Goal 的 DAG（Phase 15 新增；只读，"
        "不修改任何 Goal 状态）",
    )
    parser.add_argument(
        "--goal-graph-format",
        type=str,
        default="mermaid",
        choices=["mermaid", "json", "dot"],
        help="DAG 可视化格式（mermaid / json / dot；默认 mermaid）",
    )
    parser.add_argument(
        "--goal-graph-output",
        type=str,
        default=None,
        help="DAG 可视化输出文件路径（默认 stdout；" "路径必须在 project_root 内）",
    )
    parser.add_argument(
        "--goal-graph-desc-max",
        type=int,
        default=100,
        help="节点 description 截断长度（默认 100）",
    )

    # Phase 17 v3 新增：hot-reload 互斥 group + 路径校验 + 轮询间隔
    # v3 P0-1：--hot-reload 与 --no-hot-reload 必须互斥（argparse 强制）
    hot_reload_group = parser.add_mutually_exclusive_group()
    hot_reload_group.add_argument(
        "--hot-reload",
        dest="hot_reload",
        action="store_true",
        default=True,
        help="Phase 17 启用插件热加载（默认开启；与 --no-hot-reload 互斥）",
    )
    hot_reload_group.add_argument(
        "--no-hot-reload",
        dest="hot_reload",
        action="store_false",
        help="禁用插件热加载（生产环境推荐；与 --hot-reload 互斥）",
    )
    # v3 P0-7 第一层防护：CLI 校验路径（绝对 / '..' 拒绝）
    parser.add_argument(
        "--hot-reload-dir",
        type=_validate_drop_in_dir,
        default="plugins_extra",
        help="drop-in 目录路径（相对 project_root；不含 ..；默认 plugins_extra/）",
    )
    parser.add_argument(
        "--hot-reload-interval",
        type=float,
        default=5.0,
        help="轮询间隔（秒；默认 5.0；范围 [0.5, 60.0]，由 watcher 钳制）",
    )

    # Phase 18 新增：Ralph 风格 autonomous 模式
    # --autonomous：启用自主迭代执行器
    parser.add_argument(
        "--autonomous",
        action="store_true",
        default=False,
        help="Phase 18 启用 Ralph 风格 autonomous 模式（与 --loop / --multi-goal "
        "/ --goal-cancel / --goal-graph / --goal-resume 互斥）。"
        "autonomous 模式让多角色团队在用户睡眠时自动完成全部任务"
        "（自动运行、自动确认、自动使用 skill、自动测试、自动提交）。",
    )
    parser.add_argument(
        "--auto-max-iterations",
        type=int,
        default=50,
        help="autonomous 模式最大迭代次数（硬上限；默认 50，范围 [1, 1000]）",
    )
    parser.add_argument(
        "--auto-max-tokens",
        type=int,
        default=0,
        help="autonomous 模式 token 预算（0=不限制，默认 0；正整数=显式预算上限）",
    )
    parser.add_argument(
        "--auto-stop-when",
        type=str,
        default="",
        help='autonomous 模式自然语言停止条件（如 "all tests pass"）',
    )
    parser.add_argument(
        "--auto-test-command",
        type=str,
        default='python3 -m unittest discover -s tests -p "test_*.py"',
        help="autonomous 模式测试命令（默认：python3 -m unittest discover）",
    )
    parser.add_argument(
        "--auto-stage-order",
        type=str,
        default="plan,dev,verify,fix",
        help="autonomous 模式阶段顺序（CSV；默认 plan,dev,verify,fix）",
    )
    parser.add_argument(
        "--auto-backoff-base",
        type=float,
        default=1.0,
        help="autonomous 模式失败退避基数（秒；默认 1.0）",
    )
    parser.add_argument(
        "--auto-backoff-max",
        type=float,
        default=60.0,
        help="autonomous 模式退避上限（秒；默认 60.0）",
    )
    parser.add_argument(
        "--auto-failure-abort",
        type=int,
        default=3,
        help="autonomous 模式连续失败 abort 阈值（默认 3）",
    )
    parser.add_argument(
        "--auto-resume",
        type=str,
        default=None,
        help="autonomous 模式 resume 指定 run_id（None = 新建）",
    )
    parser.add_argument(
        "--auto-resume-latest",
        action="store_true",
        default=False,
        help="autonomous 模式 resume 最新可续跑的 run（与 --auto-resume 互斥）",
    )
    parser.add_argument(
        "--auto-no-caffeinate",
        action="store_true",
        default=False,
        help="autonomous 模式禁用 caffeinate / systemd-inhibit（CI 环境）",
    )
    parser.add_argument(
        "--auto-no-commit",
        action="store_true",
        default=False,
        help="autonomous 模式禁用自动 git commit（仅记录，不提交）",
    )
    parser.add_argument(
        "--auto-confirm-mode",
        type=str,
        default="smart",
        choices=["smart", "whitelist-only", "blacklist-only"],
        help="autonomous 模式确认模式（smart/whitelist-only/blacklist-only；默认 smart）",
    )
    parser.add_argument(
        "--auto-run-dir",
        type=str,
        default=".gnhf/runs",
        help="autonomous 模式 run 状态目录（相对 project_root；默认 .gnhf/runs）",
    )
    parser.add_argument(
        "--auto-git-author-name",
        type=str,
        default="Ralph Autonomous Agent",
        help='autonomous 模式 git commit 作者名（默认 "Ralph Autonomous Agent"）',
    )
    parser.add_argument(
        "--auto-git-author-email",
        type=str,
        default="ralph@trae-multi-agent.local",
        help="autonomous 模式 git commit 作者邮箱（默认 ralph@trae-multi-agent.local）",
    )
    parser.add_argument(
        "--auto-security-analyzer",
        type=str,
        default="builtin",
        choices=["builtin", "bandit", "semgrep"],
        help="autonomous 模式安全分析器（builtin/bandit/semgrep；默认 builtin）",
    )
    parser.add_argument(
        "--auto-notes-path",
        type=str,
        default="notes.md",
        help="autonomous 模式 notes 文件名（默认 notes.md）",
    )
    parser.add_argument(
        "--auto-max-size-kb",
        type=int,
        default=1024,
        help="autonomous 模式 notes.md 最大大小（KB；超过则 trim；默认 1024）",
    )
    parser.add_argument(
        "--auto-trim-keep-last-n",
        type=int,
        default=20,
        help="autonomous 模式 trim 时保留最近 N 段（默认 20）",
    )

    # Loop Engineering 模式 flags
    parser.add_argument(
        "--loop-engineering",
        action="store_true",
        default=False,
        help="启用 Loop Engineering 五步闭环模式",
    )
    parser.add_argument(
        "--loop-type",
        type=str,
        default="coding",
        choices=["design", "coding", "testing"],
        help="Loop 类型（默认 coding）",
    )
    parser.add_argument(
        "--loop-discovery",
        type=str,
        default="auto",
        choices=["auto", "manual", "off"],
        help="Discovery 模式（默认 auto）",
    )
    parser.add_argument(
        "--loop-evaluator",
        type=str,
        default="strict",
        choices=["strict", "standard", "off"],
        help="Evaluator 严格程度（默认 strict）",
    )
    parser.add_argument(
        "--loop-human-checkpoint-every",
        type=int,
        default=5,
        help="人类检查点间隔轮数（0=关闭；默认 5）",
    )
    parser.add_argument(
        "--loop-max-iterations",
        type=int,
        default=50,
        help="Loop Engineering 最大迭代次数（默认 50）",
    )
    parser.add_argument(
        "--loop-max-tokens",
        type=int,
        default=0,
        help="Loop Engineering Token 预算（0=不限制，默认 0；正整数=显式预算上限）",
    )
    parser.add_argument(
        "--loop-sampling-read-ratio",
        type=float,
        default=0.1,
        help="抽样阅读比例（默认 0.1）",
    )
    parser.add_argument(
        "--loop-stop-when",
        type=str,
        default="",
        help="Loop Engineering 自然语言停止条件",
    )

    return parser.parse_args()


__all__ = ["parse_arguments"]
