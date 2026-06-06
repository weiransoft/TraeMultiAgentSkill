"""V3 Legacy 入口包：完整搬迁 god module 5 个 dispatch_*_with_* 函数。

B-1 修复：将 5 个 dispatch 函数从 trae_agent_dispatch_v2.py 迁出，
消除 facade ↔ 薄壳循环依赖。
"""
