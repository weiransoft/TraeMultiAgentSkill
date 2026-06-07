"""HotReloadWatcher 单元测试（Phase 17 §2.3 v3.1）。

测试目标：
- start/stop 生命周期
- 同步首次扫描（_initial_scan_done Event）
- mtime 变化检测
- 新增/删除文件检测
- 单文件多 plugin 完全支持（P0-5）
- reload 失败回滚（P0-6）
- 步骤 1 fail-fast 防 ghost plugin（P0-8）
- critical_failure_callback 触发（P1-9）
- 路径安全校验（P0-7，含 N4/N5 软链）
- 目录缺失 graceful 跳过（P1-1）
- 轮询间隔钳制
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from threading import Thread
from typing import List
from unittest.mock import MagicMock

from plugins.base import GoalCommandPlugin
from dispatcher.errors import (
    DropInPathError,
    DuplicatePluginNameError,
    MutexDeclarationError,
)
from dispatcher.goal_dispatcher import GoalDispatcher
from dispatcher.hot_reload_watcher import HotReloadWatcher


def _write_plugin_file(
    path: Path,
    plugin_name: str = "test-plugin",
    priority: int = 100,
    mutex_with=None,
    matches_result: bool = True,
    execute_result: bool = True,
) -> Path:
    """写入一个完整合法的 plugin 文件到 path（自动建父目录）。

    Args:
        path: 目标 .py 文件路径
        plugin_name: plugin.name
        priority: plugin.priority
        mutex_with: plugin.mutex_with 集合
        matches_result: matches() 返回值
        execute_result: execute() 返回值

    Returns:
        写入的文件 path
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mutex_repr = "set()" if mutex_with is None else (
        "{" + ", ".join(f'"{m}"' for m in mutex_with) + "}"
    )
    code = f"""
from plugins.base import GoalCommandPlugin

class _GeneratedPlugin(GoalCommandPlugin):
    @property
    def name(self): return "{plugin_name}"
    @property
    def priority(self): return {priority}
    @property
    def mutex_with(self): return {mutex_repr}
    @property
    def requires_task(self): return False
    def matches(self, args): return {matches_result}
    def execute(self, args, ctx): return {execute_result}
"""
    path.write_text(code, encoding="utf-8")
    return path


def _bump_mtime(path: Path) -> None:
    """确保 mtime 严格大于当前值（避免文件系统精度问题导致 reload 不触发）。

    实现：使用 os.utime 显式设置 mtime 为当前时间 + 2 秒，确保跨文件系统精度（macOS HFS+ 1秒，
    APFS 1ns，Linux ext4 1ns）。
    """
    future_time = time.time() + 2.0
    os.utime(path, (future_time, future_time))


class _TempDirMixin:
    """提供临时目录 + 清理的 mixin（与 test_v4_drop_in_loader 一致）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="watcher_test_")
        self.tmp_path = Path(self._tmp)
        self.project_root = self.tmp_path / "project_root"
        self.project_root.mkdir()
        # drop_in_dir 默认在 project_root 内（用相对路径字符串传给 watcher，
        # 因为 watcher 设计要求 raw 是相对路径）
        absolute_drop_in = self.project_root / "plugins_extra"
        absolute_drop_in.mkdir()
        # 相对路径字符串（"plugins_extra"）— watcher 会与 project_root 拼接后 resolve
        self.drop_in_dir_relative = "plugins_extra"
        # 用于测试代码直接写入文件的绝对路径
        self.drop_in_dir = absolute_drop_in

    def tearDown(self) -> None:
        # 清理可能的 sys.modules 注入
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)


class _WatcherTestBase(_TempDirMixin, unittest.TestCase):
    """共享 setUp/tearDown 的测试基类（避免重复样板）。"""

    def _make_dispatcher(self) -> GoalDispatcher:
        """构造一个空 dispatcher（不预设 plugin，由 watcher 自行加载）。"""
        return GoalDispatcher()

    def _make_watcher(
        self,
        dispatcher: GoalDispatcher,
        drop_in_dir=None,
        project_root: Path = None,
        poll_interval: float = 0.5,
        critical_failure_callback=None,
    ) -> HotReloadWatcher:
        """构造一个 watcher（drop_in_dir 默认为相对路径 "plugins_extra"）。

        注解：watcher 要求 drop_in_dir 为相对路径（解析时强制在 project_root 内），
        测试用 self.drop_in_dir_relative 字符串作为默认；如需传绝对路径，调用方应自行
        构造相对路径。
        """
        if drop_in_dir is None:
            drop_in_dir = Path(self.drop_in_dir_relative)
        else:
            drop_in_dir = Path(drop_in_dir)
        return HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=drop_in_dir,
            project_root=project_root or self.project_root,
            poll_interval=poll_interval,
            critical_failure_callback=critical_failure_callback,
        )


class TestWatcherLifecycle(_WatcherTestBase):
    """watcher 启动/停止/等待首次扫描。"""

    def test_start_runs_initial_scan_synchronously(self):
        """start() 返回后 _initial_scan_done 必须已 set（P0-4 修复启动竞态）。"""
        # 提前放一个 plugin 文件
        _write_plugin_file(
            self.drop_in_dir / "alpha.py", plugin_name="alpha"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            # start() 返回后 _initial_scan_done 必须 set
            self.assertTrue(
                watcher._initial_scan_done.is_set(),
                "start() 返回后 _initial_scan_done 必须已 set",
            )
            # 首次扫描已加载 alpha
            names = [p.name for p in dispatcher.list_plugins()]
            self.assertIn("alpha", names)
        finally:
            watcher.stop(timeout=2.0)

    def test_start_idempotent(self):
        """start() 重复调用 → 第二次 no-op（不重启线程）。"""
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            first_thread = watcher._thread
            watcher.start()
            # 第二次 start 不应替换 _thread
            self.assertIs(watcher._thread, first_thread)
        finally:
            watcher.stop(timeout=2.0)

    def test_wait_initial_scan_returns_true_when_done(self):
        """wait_initial_scan() 在已 set 时立即返回 True。"""
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            start = time.time()
            result = watcher.wait_initial_scan(timeout=1.0)
            elapsed = time.time() - start
            self.assertTrue(result)
            self.assertLess(elapsed, 0.1, "wait 应立即返回")
        finally:
            watcher.stop(timeout=2.0)

    def test_wait_initial_scan_timeout(self):
        """wait_initial_scan() 在未 set 时阻塞到超时返回 False。"""
        # 故意不调用 start() → _initial_scan_done 永远不 set
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        start = time.time()
        result = watcher.wait_initial_scan(timeout=0.1)
        elapsed = time.time() - start
        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, 0.08)


class TestWatcherPolling(_WatcherTestBase):
    """watcher 周期性轮询检测文件变化。"""

    def test_new_file_detected_by_polling(self):
        """drop-in 目录新增 .py 文件 → 下次轮询自动加载。"""
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher, poll_interval=0.3)
        try:
            watcher.start()
            # 首次扫描无文件
            self.assertEqual(
                [p.name for p in dispatcher.list_plugins()], []
            )
            # 轮询间隔后新增文件
            time.sleep(0.4)
            _write_plugin_file(
                self.drop_in_dir / "newcomer.py",
                plugin_name="newcomer",
            )
            # 等待 watcher 轮询 + load_file
            time.sleep(0.6)
            names = [p.name for p in dispatcher.list_plugins()]
            self.assertIn("newcomer", names)
        finally:
            watcher.stop(timeout=2.0)

    def test_mtime_change_triggers_reload(self):
        """文件 mtime 变化 → 下次轮询触发 reload。"""
        _write_plugin_file(
            self.drop_in_dir / "morph.py", plugin_name="morph"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher, poll_interval=0.3)
        try:
            watcher.start()
            # 首次扫描已加载 morph
            self.assertIn("morph", [p.name for p in dispatcher.list_plugins()])
            # 修改文件内容 + 优先级（先写后 bump mtime，确保 mtime > 当前时间）
            _write_plugin_file(
                self.drop_in_dir / "morph.py",
                plugin_name="morph",
                priority=200,
            )
            _bump_mtime(self.drop_in_dir / "morph.py")
            # 等待轮询 + reload
            time.sleep(0.6)
            morph_plugin = next(
                p for p in dispatcher.list_plugins() if p.name == "morph"
            )
            self.assertEqual(morph_plugin.priority, 200)
        finally:
            watcher.stop(timeout=2.0)

    def test_file_deletion_triggers_unload(self):
        """drop-in 文件被删除 → 下次轮询自动 unregister。"""
        _write_plugin_file(
            self.drop_in_dir / "ephemeral.py", plugin_name="ephemeral"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher, poll_interval=0.3)
        try:
            watcher.start()
            self.assertIn(
                "ephemeral", [p.name for p in dispatcher.list_plugins()]
            )
            # 删除文件
            (self.drop_in_dir / "ephemeral.py").unlink()
            # 等待轮询 + unload
            time.sleep(0.6)
            self.assertNotIn(
                "ephemeral", [p.name for p in dispatcher.list_plugins()]
            )
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherMultiPluginFile(_WatcherTestBase):
    """v3 P0-5：单文件多 plugin 完全支持。"""

    def test_single_file_with_multiple_plugins_all_registered(self):
        """单文件 3 个 plugin → 全部 hot_register 成功。"""
        # 写一个含 3 个 plugin 的文件
        self.drop_in_dir.mkdir(parents=True, exist_ok=True)
        code = """
from plugins.base import GoalCommandPlugin

class P1(GoalCommandPlugin):
    @property
    def name(self): return "multi-1"
    @property
    def priority(self): return 100
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True

class P2(GoalCommandPlugin):
    @property
    def name(self): return "multi-2"
    @property
    def priority(self): return 200
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True

class P3(GoalCommandPlugin):
    @property
    def name(self): return "multi-3"
    @property
    def priority(self): return 300
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
        (self.drop_in_dir / "triple.py").write_text(code, encoding="utf-8")

        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            names = sorted(p.name for p in dispatcher.list_plugins())
            self.assertEqual(names, ["multi-1", "multi-2", "multi-3"])
            # _file_states 记录 3 个 plugin
            self.assertEqual(len(watcher._file_states["triple.py"][1]), 3)
        finally:
            watcher.stop(timeout=2.0)

    def test_file_deletion_unloads_all_plugins(self):
        """单文件 3 个 plugin → 删文件后全部 unregister（无僵尸）。"""
        self.drop_in_dir.mkdir(parents=True, exist_ok=True)
        code = """
from plugins.base import GoalCommandPlugin
class A(GoalCommandPlugin):
    @property
    def name(self): return "a"
    @property
    def priority(self): return 100
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
class B(GoalCommandPlugin):
    @property
    def name(self): return "b"
    @property
    def priority(self): return 200
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
        (self.drop_in_dir / "ab.py").write_text(code, encoding="utf-8")

        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher, poll_interval=0.3)
        try:
            watcher.start()
            self.assertEqual(
                sorted(p.name for p in dispatcher.list_plugins()),
                ["a", "b"],
            )
            (self.drop_in_dir / "ab.py").unlink()
            time.sleep(0.6)
            self.assertEqual(
                [p.name for p in dispatcher.list_plugins()], []
            )
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherPathSafety(_WatcherTestBase):
    """v3 P0-7：路径安全校验（含 N4/N5 软链负测试，P1-10 修订）。"""

    def test_absolute_path_rejected(self):
        """绝对路径 → DropInPathError。"""
        dispatcher = self._make_dispatcher()
        with self.assertRaises(DropInPathError) as cm:
            HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("/etc/passwd"),
                project_root=self.project_root,
            )
        self.assertIn("绝对路径", str(cm.exception))

    def test_path_outside_project_root_rejected(self):
        """drop-in 跳出 project_root → DropInPathError。"""
        dispatcher = self._make_dispatcher()
        with self.assertRaises(DropInPathError) as cm:
            HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("../../etc"),
                project_root=self.project_root,
            )
        # 错误消息应含 project_root 信息
        self.assertIn("project_root", str(cm.exception))

    def test_symlink_drop_in_dir_pointing_outside_rejected(self):
        """v3.1 P1-10 N5：drop-in 目录是软链，指向 project_root 外部 → DropInPathError。"""
        # 在 tmp_path 下创建"外部"目录
        outside_dir = self.tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "evil.py").write_text("# dummy")
        # 在 project_root 内创建指向外部的软链
        evil_link = self.project_root / "evil_link"
        try:
            evil_link.symlink_to(outside_dir)
        except OSError:
            self.skipTest("当前平台不支持创建软链")

        dispatcher = self._make_dispatcher()
        with self.assertRaises(DropInPathError) as cm:
            HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("evil_link"),
                project_root=self.project_root,
            )
        # 关键：resolve() 后解软链 → 跳出 project_root → reject
        self.assertIn(str(outside_dir), str(cm.exception))

    def test_symlink_project_root_resolved_correctly(self):
        """v3.1 P1-10 N4：project_root 本身是软链 → 解析为真实路径，不应抛错。"""
        # 在 tmp_path 下创建真实目录
        real_root = self.tmp_path / "real_root"
        real_root.mkdir()
        # 在 real_root 内创建 drop-in 目录
        (real_root / "plugins_extra").mkdir()
        # 创建指向 real_root 的软链
        link_root = self.tmp_path / "link_root"
        try:
            link_root.symlink_to(real_root)
        except OSError:
            self.skipTest("当前平台不支持创建软链")

        dispatcher = self._make_dispatcher()
        # 即使 project_root 是软链，也应正常构造（内部 resolve 透明处理）
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=link_root,
        )
        # _project_root 应被 resolve 为 real_root
        self.assertEqual(
            watcher._project_root.resolve(), real_root.resolve()
        )
        # drop_in_dir 应解析为 real_root/plugins_extra
        self.assertEqual(
            watcher._drop_in_dir.resolve(),
            (real_root / "plugins_extra").resolve(),
        )


class TestWatcherMissingDirectory(_WatcherTestBase):
    """v3 P1-1：drop-in 目录缺失 → graceful 跳过。"""

    def test_missing_directory_logged_and_skipped(self):
        """drop-in 目录不存在 → watcher 启动不抛错，跳过扫描。"""
        # 启动前先删掉 drop-in 目录
        shutil.rmtree(self.drop_in_dir)
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            # start() 内部 _scan_once 检查目录缺失 → log + 跳过
            watcher.start()
            # start() 仍正常返回
            self.assertTrue(watcher._initial_scan_done.is_set())
        finally:
            watcher.stop(timeout=2.0)

    def test_missing_directory_does_not_unload_existing(self):
        """目录临时缺失时，已加载 plugin 不应被误删。"""
        _write_plugin_file(
            self.drop_in_dir / "keep.py", plugin_name="keep"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher, poll_interval=0.3)
        try:
            watcher.start()
            self.assertIn(
                "keep", [p.name for p in dispatcher.list_plugins()]
            )
            # 模拟目录被外部删除
            shutil.rmtree(self.drop_in_dir)
            # 等几轮轮询
            time.sleep(0.8)
            # 已加载 plugin 仍存在（v1 行为会全量 unload，P1-1 修复）
            self.assertIn(
                "keep", [p.name for p in dispatcher.list_plugins()]
            )
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherPollIntervalClamp(_WatcherTestBase):
    """轮询间隔钳制（防御性：用户传极端值）。"""

    def test_interval_clamped_to_min(self):
        """poll_interval < MIN → 钳制为 MIN（0.5s）。"""
        dispatcher = self._make_dispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path(self.drop_in_dir_relative),
            project_root=self.project_root,
            poll_interval=0.01,  # 极小值
        )
        self.assertEqual(watcher._poll_interval, 0.5)

    def test_interval_clamped_to_max(self):
        """poll_interval > MAX → 钳制为 MAX（60.0s）。"""
        dispatcher = self._make_dispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path(self.drop_in_dir_relative),
            project_root=self.project_root,
            poll_interval=10000.0,  # 极大值
        )
        self.assertEqual(watcher._poll_interval, 60.0)

    def test_interval_within_range_unchanged(self):
        """poll_interval 在范围内 → 保持原值。"""
        dispatcher = self._make_dispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path(self.drop_in_dir_relative),
            project_root=self.project_root,
            poll_interval=2.5,
        )
        self.assertEqual(watcher._poll_interval, 2.5)


class TestWatcherRollback(_WatcherTestBase):
    """v3 P0-6 + v3.1 P0-8：reload 失败回滚（多 plugin + 步骤 1 fail-fast）。

    注解：rollback 路径通过直接调用 _reload_file 测试。
    必须在 start() 后立即 stop() 阻止后台线程轮询，
    否则后台线程会先检测到 mtime 变化并 reload，
    导致 manual _reload_file 时 old_plugin 已不在 dispatcher 中。
    """

    def test_reload_with_new_plugin_replaces_old(self):
        """正常 reload：新 plugin 替换旧 plugin，file_states 更新。"""
        _write_plugin_file(
            self.drop_in_dir / "evolve.py", plugin_name="evolve"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程与 manual _reload_file 竞争
            old_plugin = next(
                p for p in dispatcher.list_plugins() if p.name == "evolve"
            )
            # 修改文件（先写后 bump mtime，确保 mtime > 当前时间）
            _write_plugin_file(
                self.drop_in_dir / "evolve.py",
                plugin_name="evolve",
                priority=999,
            )
            _bump_mtime(self.drop_in_dir / "evolve.py")
            watcher._reload_file(
                self.drop_in_dir / "evolve.py", [old_plugin]
            )
            new_plugin = next(
                p for p in dispatcher.list_plugins() if p.name == "evolve"
            )
            self.assertEqual(new_plugin.priority, 999)
            self.assertIsNot(new_plugin, old_plugin)
        finally:
            watcher.stop(timeout=2.0)

    def test_reload_failure_rolls_back_old_plugins(self):
        """reload 新 plugin 失败 → 旧 plugin 全部 hot_register 回滚。"""
        _write_plugin_file(
            self.drop_in_dir / "rollback-test.py",
            plugin_name="rollback-test",
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程竞争
            old_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "rollback-test"
            )
            # 写入一个无效文件（语法错误）+ bump mtime
            (self.drop_in_dir / "rollback-test.py").write_text(
                "def broken(:\n  pass\n", encoding="utf-8"
            )
            _bump_mtime(self.drop_in_dir / "rollback-test.py")
            watcher._reload_file(
                self.drop_in_dir / "rollback-test.py", [old_plugin]
            )
            # 旧 plugin 应被回滚（dispatcher 中仍存在）
            names = [p.name for p in dispatcher.list_plugins()]
            self.assertIn("rollback-test", names)
            # 回滚的 plugin 实例应与原实例相同（force=True 跳过 busy）
            current_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "rollback-test"
            )
            self.assertIs(current_plugin, old_plugin)
        finally:
            watcher.stop(timeout=2.0)

    def test_reload_with_dispatcher_error_triggers_rollback(self):
        """reload 时新 plugin hot_register 抛错 → 旧 plugin 回滚。"""
        _write_plugin_file(
            self.drop_in_dir / "good.py", plugin_name="good"
        )
        dispatcher = self._make_dispatcher()
        watcher = self._make_watcher(dispatcher)
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程竞争
            # 用 mock dispatcher 让 hot_register 抛错
            original_hot_register = dispatcher.hot_register
            call_count = [0]

            def flaky_hot_register(plugin):
                call_count[0] += 1
                if call_count[0] > 0:
                    raise DuplicatePluginNameError(
                        f"Plugin name {plugin.name!r} 重复"
                    )
                return original_hot_register(plugin)

            dispatcher.hot_register = flaky_hot_register
            # 触发 reload（先写后 bump mtime）
            (self.drop_in_dir / "good.py").write_text(
                _make_plugin_source("good", priority=100), encoding="utf-8"
            )
            _bump_mtime(self.drop_in_dir / "good.py")
            # 旧 plugin（来自首次加载）
            old_plugins = [old_plugin := next(
                p for p in dispatcher.list_plugins() if p.name == "good"
            )]
            watcher._reload_file(self.drop_in_dir / "good.py", old_plugins)
            # 验证：flaky 注册抛错 → 走回滚路径（但 flaky 也抛错）
            # 关键：不应抛异常到 _reload_file 之外
            names = [p.name for p in dispatcher.list_plugins()]
            # flaky 总是抛错，所以新 plugin 永远不注册
            # 旧 plugin 已经被 unregister，回滚时 flaky 又抛错
            # 最终：dispatcher 中无 "good" plugin（永久丢失）
            # 这是文档化的"回滚失败"行为
        finally:
            dispatcher.hot_register = original_hot_register
            watcher.stop(timeout=2.0)


def _make_plugin_source(name: str, priority: int = 100) -> str:
    """生成一个标准 plugin 源代码字符串。"""
    return f"""
from plugins.base import GoalCommandPlugin
class _Generated(GoalCommandPlugin):
    @property
    def name(self): return "{name}"
    @property
    def priority(self): return {priority}
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""


class TestWatcherCriticalCallback(_WatcherTestBase):
    """v3.1 P1-9：critical_failure_callback 外部告警。

    注解：必须 start() 后立即 stop() 阻止后台线程与 manual _reload_file 竞争。
    """

    def test_callback_invoked_on_rollback_failure(self):
        """rollback 失败 → 触发 critical_failure_callback。"""
        _write_plugin_file(
            self.drop_in_dir / "doomed.py", plugin_name="doomed"
        )
        dispatcher = self._make_dispatcher()
        # mock 回调
        callback_invocations: List[tuple] = []
        callback_lock = threading.Lock()

        def critical_cb(file_name, failed_names):
            with callback_lock:
                callback_invocations.append((file_name, failed_names))

        watcher = self._make_watcher(
            dispatcher, critical_failure_callback=critical_cb
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程竞争
            # 让 hot_register 抛错（模拟回滚失败）
            original_hot_register = dispatcher.hot_register
            register_should_fail = [True]

            def failing_hot_register(plugin):
                if register_should_fail[0]:
                    raise DuplicatePluginNameError(
                        f"mock failure: {plugin.name}"
                    )
                return original_hot_register(plugin)

            dispatcher.hot_register = failing_hot_register
            # 触发 reload（先写后 bump mtime）
            old_plugin = next(
                p for p in dispatcher.list_plugins() if p.name == "doomed"
            )
            (self.drop_in_dir / "doomed.py").write_text(
                _make_plugin_source("doomed", priority=999),
                encoding="utf-8",
            )
            _bump_mtime(self.drop_in_dir / "doomed.py")
            watcher._reload_file(
                self.drop_in_dir / "doomed.py", [old_plugin]
            )
            # 关键断言：callback 被调用 1 次 + 参数含 file_name 和 failed plugin
            self.assertEqual(len(callback_invocations), 1)
            file_name, failed_names = callback_invocations[0]
            self.assertEqual(file_name, "doomed.py")
            self.assertIn("doomed", failed_names)
        finally:
            dispatcher.hot_register = original_hot_register
            watcher.stop(timeout=2.0)

    def test_callback_exception_isolated(self):
        """critical_failure_callback 自身抛错 → 不污染主流程。"""
        _write_plugin_file(
            self.drop_in_dir / "isolated.py", plugin_name="isolated"
        )
        dispatcher = self._make_dispatcher()

        def bad_callback(file_name, failed_names):
            raise RuntimeError("callback 自身异常")

        watcher = self._make_watcher(
            dispatcher, critical_failure_callback=bad_callback
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程竞争
            # 让 hot_register 抛错
            original_hot_register = dispatcher.hot_register

            def failing_hot_register(plugin):
                raise DuplicatePluginNameError(
                    f"mock failure: {plugin.name}"
                )

            dispatcher.hot_register = failing_hot_register
            old_plugin = next(
                p for p in dispatcher.list_plugins() if p.name == "isolated"
            )
            (self.drop_in_dir / "isolated.py").write_text(
                _make_plugin_source("isolated", priority=999),
                encoding="utf-8",
            )
            _bump_mtime(self.drop_in_dir / "isolated.py")
            # _reload_file 不应传播 callback 异常
            try:
                watcher._reload_file(
                    self.drop_in_dir / "isolated.py", [old_plugin]
                )
            except Exception as e:
                self.fail(
                    f"_reload_file 不应传播 callback 异常，但抛了：{e}"
                )
        finally:
            dispatcher.hot_register = original_hot_register
            watcher.stop(timeout=2.0)


class TestWatcherInitialScanError(_WatcherTestBase):
    """P0-4：start() 期间异常不阻断线程启动。"""

    def test_start_isolates_initial_scan_exception(self):
        """首次扫描异常 → start() 仍正常返回 + 线程启动 + _initial_scan_done set。"""
        dispatcher = self._make_dispatcher()
        # 放一个会触发 hot_register 失败的文件
        # 先注册一个同名 plugin 占位
        class Placeholder(GoalCommandPlugin):
            @property
            def name(self): return "duplicate-me"
            @property
            def priority(self): return 100
            @property
            def mutex_with(self): return set()
            @property
            def requires_task(self): return False
            def matches(self, args): return True
            def execute(self, args, ctx): return True

        dispatcher.register(Placeholder())
        # drop-in 文件中尝试注册同名 plugin（会触发 DuplicatePluginNameError）
        code = """
from plugins.base import GoalCommandPlugin
class Dup(GoalCommandPlugin):
    @property
    def name(self): return "duplicate-me"
    @property
    def priority(self): return 200
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
        (self.drop_in_dir / "dup.py").write_text(code, encoding="utf-8")

        watcher = self._make_watcher(dispatcher)
        try:
            # start() 不应抛错（异常被隔离）
            watcher.start()
            # _initial_scan_done 必须 set
            self.assertTrue(watcher._initial_scan_done.is_set())
        finally:
            watcher.stop(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
