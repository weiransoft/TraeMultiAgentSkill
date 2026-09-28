"""SU 软依赖 try-import 探测与降级报告（架构 ARCH-SU-001 §2.3.14，REQ-SU-021）。

软依赖（playwright / pymysql / psycopg2 / redis）绝不在模块顶层硬 import——
本模块是唯一集中探测点：probe_all() 以可注入的 import_fn 逐个 try-import，
产出 DependencyReport 供 preflight 与编排层共享。

降级口径（REQ-SU-021）：
  - playwright 缺失 = 致命（require_playwright 抛 SuDepsError，exit=5，含安装命令）；
  - pymysql/psycopg2/redis 缺失 = 可降级（require_* 返回 None，由调用方登记
    降级记录，文档显式声明"未采集：驱动缺失（安装命令）"，绝不以空数据冒充）。

显式注入点（2026-09-28 审查）：probe_all(import_fn=...) 默认
importlib.import_module；单测注入 fake importer 即可确定性制造缺失组合，
无需 monkeypatch 全局 import 机制（test_su_degrade）。
"""

import importlib
import types
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from su.dto import SuDepsError

__all__ = ["DependencyReport", "probe_all"]

# 安装命令常量（报错文案与 PRD REQ-SU-021 表格逐字对齐，含引号防 shell 展开）
_PLAYWRIGHT_INSTALL_HINT = (
    "pip install 'playwright>=1.40.0' && playwright install chromium"
)
_PYMYSQL_INSTALL_HINT = "pip install 'pymysql>=1.1.0'"
_PSYCOPG2_INSTALL_HINT = "pip install 'psycopg2-binary>=2.9'"
_REDIS_INSTALL_HINT = "pip install 'redis>=5.0.0'"

# engine → (驱动模块名, 安装命令) 映射（DatabaseConfig.engine 口径）
_DB_DRIVER_MAP = {
    "mysql": ("pymysql", _PYMYSQL_INSTALL_HINT),
    "postgresql": ("psycopg2", _PSYCOPG2_INSTALL_HINT),
}


@dataclass
class DependencyReport:
    """软依赖探测结果（preflight 与编排层共享的降级事实源）。

    各字段语义：探测成功=模块对象；缺失=None。missing 记录缺失模块名列表，
    供渲染器输出"未采集：驱动缺失（安装命令）"的显式缺失声明（AP-3）。
    """

    playwright: Optional[types.ModuleType] = None   # UI 透镜（致命依赖）
    pymysql: Optional[types.ModuleType] = None      # MySQL 驱动（可降级）
    psycopg2: Optional[types.ModuleType] = None     # PostgreSQL 驱动（可降级）
    redis: Optional[types.ModuleType] = None        # Redis 驱动（可降级）
    missing: List[str] = field(default_factory=list)  # 缺失模块名列表

    def require_playwright(self) -> types.ModuleType:
        """取 playwright 模块；缺失即致命降级（REQ-SU-021 第一行）。

        Returns:
            types.ModuleType: playwright 模块对象。

        Raises:
            SuDepsError: playwright 未安装——中文报错含完整安装命令，
                退出码 5，调用方不得产出任何编造的 UI 结论（禁 mock 红线）。
        """
        if self.playwright is None:
            raise SuDepsError(
                message=(
                    "未安装 playwright"
                    "（pip install 'playwright>=1.40.0' && playwright install chromium），"
                    "UI 遍历与 API 观测不可用"
                ),
                hints=[
                    _PLAYWRIGHT_INSTALL_HINT,
                    "安装后重跑；如仅需 DB/Redis 透镜请评估后续版本支持（本期 UI 为必需）",
                ],
            )
        return self.playwright

    def require_db_driver(self, engine: str) -> Optional[types.ModuleType]:
        """按 database.engine 取对应驱动；缺失返回 None（可降级，REQ-SU-021）。

        Args:
            engine: 'mysql' 或 'postgresql'（validate 已保证枚举合法）。

        Returns:
            types.ModuleType | None: 驱动模块；None=缺失，调用方必须登记
                降级记录（db 透镜 skip_reason=驱动缺失+安装命令），
                文档数据模型节输出显式缺失声明。
        """
        mapping = _DB_DRIVER_MAP.get(engine)
        if mapping is None:
            # engine 非法不应到达此处（config.validate 拦截）；防御性返回 None
            return None
        module_name, _hint = mapping
        return getattr(self, module_name, None)

    def db_driver_install_hint(self, engine: str) -> Optional[str]:
        """返回指定 engine 对应驱动的安装命令（渲染降级声明文案复用）。

        Args:
            engine: 'mysql' 或 'postgresql'。

        Returns:
            str | None: 安装命令；engine 非法时返回 None。
        """
        mapping = _DB_DRIVER_MAP.get(engine)
        return mapping[1] if mapping else None

    def require_redis(self) -> Optional[types.ModuleType]:
        """取 redis 驱动；缺失返回 None（可降级，REQ-SU-021 第三行）。

        Returns:
            types.ModuleType | None: redis 模块；None=缺失，调用方登记
                降级记录，文档第 6 节输出"未采集：Redis 驱动缺失（安装命令）"。
        """
        return self.redis

    def redis_install_hint(self) -> str:
        """返回 redis 驱动安装命令（渲染降级声明文案复用）。"""
        return _REDIS_INSTALL_HINT


def probe_all(
    import_fn: Callable[[str], types.ModuleType] = importlib.import_module,
) -> DependencyReport:
    """try-import 四个软依赖，产出降级报告（REQ-SU-021 探测唯一入口）。

    内部一律经 import_fn(name) + try/except ImportError 探测——显式注入点
    允许单测（test_su_degrade）注入 fake importer 确定性制造任意缺失组合，
    不 monkeypatch 全局 import 机制。

    Args:
        import_fn: 模块导入函数，默认 importlib.import_module。

    Returns:
        DependencyReport: 四依赖的探测结果与缺失清单（missing 按固定顺序
        playwright→pymysql→psycopg2→redis 收录，报告输出稳定可 diff）。
    """
    report = DependencyReport()
    # 逐个探测：ImportError（含子依赖缺失）一律降级为 None + missing 记录
    for attr, module_name in (
        ("playwright", "playwright.sync_api"),
        ("pymysql", "pymysql"),
        ("psycopg2", "psycopg2"),
        ("redis", "redis"),
    ):
        try:
            module = import_fn(module_name)
        except ImportError:
            module = None
        if module is None:
            # 记录用户口径的依赖名（playwright.sync_api 归一为 playwright）
            report.missing.append("playwright" if attr == "playwright" else module_name)
        # playwright 存包本体（sync_api 子模块经属性可达，与直接 import playwright 等效）
        if attr == "playwright" and module is not None:
            try:
                module = import_fn("playwright")
            except ImportError:
                # 子模块可导入但包本体不可（理论上不发生）：保持 sync_api 可用
                pass
        setattr(report, attr, module)
    return report
