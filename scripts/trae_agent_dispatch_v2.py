#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trae Agent 调度入口（V3 薄壳，< 50 行）。

Phase 16 V3 插件架构重构后，本文件仅做兼容性 re-export，
所有真实实现位于 dispatch/legacy.py + facade.py。

向后兼容承诺：
- 19 处外部 import 站点（tests / scripts）继续可用
- 11 个旧符号 100% re-export
- python3 trae_agent_dispatch_v2.py CLI 行为与 v2 一致（走 facade.main_compat）
"""
import sys

# B-1 修复：薄壳单向依赖 facade（避免循环 import）
from facade import main_compat  # noqa: F401
from facade import (  # noqa: F401
    log,
    dispatch_agent_v2,
    dispatch_agent,
    dispatch_agent_v2_with_loop_goal,
    dispatch_agent_v2_with_goal_resume,
    dispatch_agent_v2_with_multi_goal,
    dispatch_agent_v2_with_goal_cancel,
    dispatch_agent_v2_with_goal_graph,
    _is_overall_success,
    _module_level_single_dispatch,
)
from cli.parser import parse_arguments  # noqa: F401


def main() -> int:
    """CLI 入口（兼容 v2 调用方）— 委托给 facade.main_compat。

    Returns:
        int: 进程退出码（0 = 成功，1 = 失败）
    """
    return main_compat()


if __name__ == '__main__':
    sys.exit(main())
