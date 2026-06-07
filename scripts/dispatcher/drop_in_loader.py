"""DropInLoader 组件（Phase 17 §2.4）。

职责：动态加载 drop-in 目录中的 .py 文件，提取并实例化所有 GoalCommandPlugin 子类。

核心契约：
1. 动态 import：通过 importlib.util.spec_from_file_location + module_from_spec + exec_module
2. sys.modules 注入：成功路径将 module 注入到 sys.modules["plugins_extra.<sanitized_stem>"]
3. sys.modules 清理：失败路径通过 try/finally 在 finally 分支清理半成品
4. 多 plugin 支持：单文件可定义多个 GoalCommandPlugin 子类，全部实例化
5. 严格契约：必须为 GoalCommandPlugin 的具体子类（排除 ABC 自身）
6. stem sanitize：将中文/特殊字符替换为下划线，生成合法 Python identifier
7. 错误处理：文件不存在 / spec 失败 / exec_module 失败 / 无 plugin → 抛 DropInLoadError

线程安全：load_from_file 是无状态操作（仅修改全局 sys.modules），
不依赖外部锁。并发调用时由调用方（HotReloadWatcher）协调。
"""
import importlib.util
import inspect
import logging
import re
import sys
from pathlib import Path
from typing import List, Type

from plugins.base import GoalCommandPlugin
from dispatcher.errors import DropInLoadError


# 合法 Python identifier 字符集（保留 [a-zA-Z0-9_]），其余字符 → "_"
# 注解：Python identifier 实际还支持 Unicode 字母，但 sys.modules key
# 建议使用 ASCII 以避免 pickle / log 中字符编码问题。
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_]")

# 注入到 sys.modules 的命名空间前缀
_NAMESPACE_PREFIX = "plugins_extra"

# DropInLoader 专用 logger（便于 watcher 区分日志来源）
_logger = logging.getLogger("drop_in_loader")


class DropInLoader:
    """drop-in 插件加载器（v3：try/finally + sanitize + ABC 严格契约）。

    设计要点：
    - load_from_file 为静态方法，调用方无需维护实例
    - _sanitize_stem 为静态方法，可被外部测试或 watcher 直接调用
    - ABC 排除：issubclass(plugin_cls, GoalCommandPlugin) AND
      plugin_cls is not GoalCommandPlugin
    - try/finally 包裹 exec_module：失败路径必清 sys.modules 防泄漏
    """

    @staticmethod
    def _sanitize_stem(stem: str) -> str:
        """将任意字符串 sanitize 为合法 Python identifier（P2-1）。

        规则：
        - 非 [a-zA-Z0-9_] 字符 → "_"
        - 首字符若是数字 → 前缀 "_"（Python identifier 不允许数字开头）
        - 全部被替换为空 → 返回 "_"（避免 sys.modules key 为空）

        Args:
            stem: 原始字符串（通常是 path.stem，含中文/特殊字符）

        Returns:
            sanitize 后的字符串（合法 Python identifier）

        边界：
        - 空字符串 → "_"
        - 全部非合法字符 → "_"
        - 数字开头 → 前缀 "_"

        示例：
            _sanitize_stem("hello") == "hello"
            _sanitize_stem("hello-world") == "hello_world"
            _sanitize_stem("hello@x") == "hello_x"
            _sanitize_stem("插件") == "__"
            _sanitize_stem("123_numeric") == "_123_numeric"
            _sanitize_stem("") == "_"
        """
        # 步骤 1：非合法字符 → "_"
        sanitized: str = _SANITIZE_RE.sub("_", stem)
        # 步骤 2：空字符串 → "_"（避免 sys.modules key 退化为 ".xxx"）
        if not sanitized:
            sanitized = "_"
        # 步骤 3：首字符若为数字 → 前缀 "_"（确保合法 Python identifier）
        if sanitized[0].isdigit():
            sanitized = "_" + sanitized
        return sanitized

    @staticmethod
    def load_from_file(path: Path) -> List[GoalCommandPlugin]:
        """从 .py 文件动态加载所有 GoalCommandPlugin 子类并实例化（P1-4 try/finally）。

        行为契约：
        1. 校验 path 存在（否则抛 DropInLoadError）
        2. spec_from_file_location 构造 spec
        3. module_from_spec + exec_module（try/finally 包裹，失败必清 sys.modules）
        4. inspect.getmembers 找出所有 GoalCommandPlugin 子类（排除 ABC 自身）
        5. 实例化每个子类
        6. 成功 → 返回 plugin 实例列表 + sys.modules 保留 module 引用
           失败 → finally 清理 sys.modules 后抛 DropInLoadError

        Args:
            path: 待加载的 .py 文件路径（Path 对象）

        Returns:
            成功加载的 plugin 实例列表（每个实例对应一个 GoalCommandPlugin 子类）

        Raises:
            DropInLoadError: 任意加载失败（文件不存在 / spec 失败 / exec 失败 / 无 plugin）

        线程安全：仅修改全局 sys.modules；并发由调用方协调
        """
        # === 1. 校验文件存在 ===
        if not path.exists() or not path.is_file():
            raise DropInLoadError(f"文件不存在：{path}")

        # === 2. 计算 sanitized module name（sys.modules key） ===
        # path.stem 可能含中文/特殊字符 → sanitize 为合法 Python identifier
        sanitized_stem: str = DropInLoader._sanitize_stem(path.stem)
        module_name: str = f"{_NAMESPACE_PREFIX}.{sanitized_stem}"

        # === 3. 构造 spec（spec_from_file_location 可能抛 OSError / ValueError） ===
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
        except (OSError, ValueError) as e:
            raise DropInLoadError(f"spec_from_file_location 失败：{path}（{e}）") from e

        if spec is None or spec.loader is None:
            raise DropInLoadError(f"spec_from_file_location 返回 None：{path}")

        # === 4. exec_module（try/finally 包裹，失败必清 sys.modules） ===
        module = importlib.util.module_from_spec(spec)
        # 即使 exec_module 失败，也应从 sys.modules 移除半成品
        sys.modules[module_name] = module
        try:
            try:
                spec.loader.exec_module(module)
            except BaseException as exec_err:
                # 失败原因覆盖：SyntaxError / ImportError / plugin 构造时异常
                raise DropInLoadError(
                    f"exec_module 失败：{path}（{type(exec_err).__name__}：{exec_err}）"
                ) from exec_err
        except BaseException:
            # finally：清理 sys.modules 中的半成品
            sys.modules.pop(module_name, None)
            raise

        # === 5. 扫描 module 找 GoalCommandPlugin 子类（排除 ABC 自身） ===
        plugin_classes: List[Type[GoalCommandPlugin]] = []
        for _, obj in inspect.getmembers(module, inspect.isclass):
            # 排除 GoalCommandPlugin ABC 自身
            if obj is GoalCommandPlugin:
                continue
            # 排除从其他模块导入的类（非本文件定义）
            if obj.__module__ != module_name:
                continue
            # 必须是 GoalCommandPlugin 的具体子类
            if not issubclass(obj, GoalCommandPlugin):
                continue
            # 必须是具体类（不能有未实现的抽象方法）
            if inspect.isabstract(obj):
                continue
            plugin_classes.append(obj)

        if not plugin_classes:
            # finally：清理 sys.modules（plugin 未找到时，module 引用无用）
            sys.modules.pop(module_name, None)
            raise DropInLoadError(
                f"{path} 未定义任何 GoalCommandPlugin 子类"
            )

        # === 6. 实例化每个 plugin 类 ===
        plugins: List[GoalCommandPlugin] = []
        for plugin_cls in plugin_classes:
            try:
                instance = plugin_cls()
            except BaseException as inst_err:
                # 构造失败 → 清理 sys.modules + 抛 DropInLoadError
                sys.modules.pop(module_name, None)
                raise DropInLoadError(
                    f"plugin {plugin_cls.__name__} 构造失败：{inst_err}"
                ) from inst_err
            plugins.append(instance)

        # === 7. 校验 plugin name 唯一性（同一文件不应有重复 name） ===
        names = [p.name for p in plugins]
        if len(names) != len(set(names)):
            duplicates = [n for n in names if names.count(n) > 1]
            sys.modules.pop(module_name, None)
            raise DropInLoadError(
                f"{path} 包含重复 plugin name：{sorted(set(duplicates))}"
            )

        _logger.info(
            f"[DropInLoader] 加载成功：{path} → {len(plugins)} 个 plugin：{names}"
        )
        return plugins


__all__ = ["DropInLoader"]
