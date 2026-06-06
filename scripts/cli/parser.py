"""V3 CLI 解析层：从 trae_agent_dispatch_v2.py line 72-318 完整搬迁 parse_arguments()。

约束：
- 保持所有 CLI flag 与 god module 100% 等价
- 保持 --task 非必填（Phase 15 B-3 修复）
- 保持 --dry-run 行为
- 风险-11：plugin 通过 `from plugins.X import X` 引用
  dispatcher 内部用 f"--{plugin.name}" 派生 CLI flag（B-4 修复）
"""
import argparse


def parse_arguments():
    """解析 CLI 参数（保持与 god module 100% 等价）。

    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description='Trae Agent 调度脚本 v2.0 - 调度不同的智能体角色来实现任务',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 修复 Phase 15 B-3：--task 改为非必需（--goal-graph / --goal-cancel 等
    # 只读 / 状态变更模式不需要 --task）
    parser.add_argument(
        '--task',
        type=str,
        required=False,
        default="",
        help='任务描述，例如："实现 SOUL-007 专注模式切换测试用例"'
    )

    parser.add_argument(
        '--agent',
        type=str,
        choices=['architect', 'product-manager', 'tester', 'solo-coder', 'ui-designer', 'devops', 'auto'],
        default='auto',
        help='指定要调度的智能体角色（默认：auto - 自动匹配）'
    )

    parser.add_argument(
        '--project-root',
        type=str,
        default='.',
        help='项目根目录路径（默认：当前目录）'
    )

    parser.add_argument(
        '--task-file',
        type=str,
        help='任务文件路径'
    )

    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='输出文件路径（可选）'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='启用详细输出模式'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅模拟执行，不实际调用智能体'
    )

    parser.add_argument(
        '--use-v1',
        action='store_true',
        help='使用 v1.0 版本逻辑（不使用新组件）'
    )

    parser.add_argument(
        '--project-full-lifecycle',
        action='store_true',
        help='启用项目全生命周期模式（8 阶段标准工作流程：需求→架构→UI→测试→任务→开发→测试→发布）'
    )

    # Phase 11 新增：/loop + /goal 集成
    parser.add_argument(
        '--loop',
        type=int,
        default=1,
        help='循环执行次数（默认 1 = 不循环；范围 [1, 100]）'
    )

    parser.add_argument(
        '--goal',
        type=str,
        default=None,
        help='目标 ID（kebab-case，例如：fix-tests / refactor-auth）'
    )

    parser.add_argument(
        '--goal-desc',
        type=str,
        default=None,
        help='目标描述（创建新目标时必填；已存在目标可省略）'
    )

    parser.add_argument(
        '--criteria',
        action='append',
        default=[],
        help='验收标准（可多次传入，例如：--criteria "tests pass" --criteria "no warnings"）'
    )

    parser.add_argument(
        '--convergence-window',
        type=int,
        default=3,
        help='收敛窗口：连续 N 次无新产出则提前退出（默认 3）'
    )

    # Phase 13 新增：多 Goal 编排 CLI 标志
    parser.add_argument(
        '--multi-goal',
        type=str,
        default=None,
        help='以指定 root Goal ID 为入口执行多 Goal 编排（触发 DAG 调度器）',
    )
    parser.add_argument(
        '--goal-parent',
        type=str,
        default=None,
        help='创建新 Goal 时指定 parent_goal_id（多 Goal 树）',
    )
    parser.add_argument(
        '--goal-depends',
        action='append',
        default=[],
        help='为新 Goal 增加 depends_on 依赖（可多次传入，例如：--goal-depends g1 --goal-depends g2）',
    )
    parser.add_argument(
        '--goal-aggregation',
        type=str,
        default='AND',
        choices=['AND', 'OR', 'MAJORITY'],
        help='子 Goal 聚合策略（AND=全部成功 / OR=任一成功 / MAJORITY=多数成功；默认 AND）',
    )
    parser.add_argument(
        '--goal-resume',
        type=str,
        default=None,
        help='续跑指定 Goal（不带 --force 时仅续可续跑 goal）',
    )
    parser.add_argument(
        '--goal-resume-force',
        action='store_true',
        help='强制续跑（包括 ABANDONED 状态的 Goal / FAILED 续跑超限）',
    )
    parser.add_argument(
        '--goal-max-resume-count',
        type=int,
        default=3,
        help='覆盖单 Goal 续跑上限（默认 3）',
    )
    parser.add_argument(
        '--reuse-threshold',
        type=float,
        default=0.85,
        help='跨 Goal 复用相似度阈值（0.0-1.0；默认 0.85）',
    )
    parser.add_argument(
        '--disable-iteration-reuse',
        action='store_true',
        help='禁用跨 Goal iteration 语义复用',
    )
    parser.add_argument(
        '--max-concurrent',
        type=int,
        default=10,
        help='多 Goal 编排时 DAG 并发 worker 数（默认 10）',
    )
    parser.add_argument(
        '--goal-report',
        type=str,
        default=None,
        choices=['json', 'md'],
        help='多 Goal 编排完成后输出报告（json / md）',
    )

    # Phase 14 新增：--goal-cancel <goal_id>
    parser.add_argument(
        '--goal-cancel',
        type=str,
        default=None,
        help='取消指定 root Goal 及其所有子 Goal（Phase 14 完善：'
             '终止运行中子进程 + 标记 ABANDONED + 释放资源）',
    )

    # Phase 15 新增：DAG 依赖图可视化
    parser.add_argument(
        '--goal-graph',
        type=str,
        default=None,
        help='可视化指定 root Goal 的 DAG（Phase 15 新增；只读，'
             '不修改任何 Goal 状态）',
    )
    parser.add_argument(
        '--goal-graph-format',
        type=str,
        default='mermaid',
        choices=['mermaid', 'json', 'dot'],
        help='DAG 可视化格式（mermaid / json / dot；默认 mermaid）',
    )
    parser.add_argument(
        '--goal-graph-output',
        type=str,
        default=None,
        help='DAG 可视化输出文件路径（默认 stdout；'
             '路径必须在 project_root 内）',
    )
    parser.add_argument(
        '--goal-graph-desc-max',
        type=int,
        default=100,
        help='节点 description 截断长度（默认 100）',
    )

    return parser.parse_args()


__all__ = ["parse_arguments"]
