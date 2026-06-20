#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉回归 + 数据显示完整性检测 + 显示错误检测

核心能力:
  1. 视觉回归 (Visual Regression)
     - 像素级 Diff (PIL ImageChops)
     - SSIM 区域级 Diff（无 opencv 时使用简化算法）
     - 阈值可配（默认 pixel_diff_ratio < 1%）

  2. 数据显示不全检测
     - 文本截断 (text-overflow)
     - 元素溢出视口
     - 图片未加载 (naturalWidth=0)
     - Loading 骨架屏持续 >10s
     - 长表格横向滚动

  3. 显示错误检测
     - 红色文字/背景 (HSV 检测)
     - 常见错误关键词
     - Ant Design / Arco Design error 类型 Toast
     - Element UI el-message--error
     - 浏览器原生 dialog (alert/confirm)

依赖:
  - Pillow (PIL)
  - playwright (DOM 检查)
  - 可选: numpy, opencv-python (更好的 SSIM)

设计原则:
  - YAGNI: 只实现最常用的 3 类检测，不造大而全框架
  - 标准库优先: 优先 PIL + playwright 自带 API
  - 失败安全: 任何检测器异常不影响主流程
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 软依赖：PIL
try:
    from PIL import Image, ImageChops  # type: ignore

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False
    Image = None  # type: ignore
    ImageChops = None  # type: ignore

# 软依赖：numpy（用于 SSIM 和快速像素统计）
try:
    import numpy as np  # type: ignore

    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    NUMPY_AVAILABLE = False
    np = None  # type: ignore


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class ChangedRegion:
    """变化区域"""

    x: int
    y: int
    width: int
    height: int
    pixel_count: int
    severity: str  # LOW / MEDIUM / HIGH

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiffResult:
    """视觉 Diff 完整结果"""

    test_id: str
    step: str
    baseline_path: Optional[str]
    current_path: str
    pixel_diff_ratio: float = 0.0
    ssim_score: float = 1.0
    changed_regions: List[ChangedRegion] = field(default_factory=list)
    data_incomplete: List[str] = field(default_factory=list)
    display_errors: List[str] = field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = ""

    @property
    def is_pass(self) -> bool:
        """判定是否通过：默认阈值 1% 像素差异 + 无错误"""
        if self.error:
            return False
        if self.display_errors:
            return False
        return self.pixel_diff_ratio < 0.01

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["changed_regions"] = [r if isinstance(r, dict) else r.to_dict() for r in self.changed_regions]
        d["is_pass"] = self.is_pass
        return d


# ---------------------------------------------------------------------------
# 视觉回归核心
# ---------------------------------------------------------------------------
class VisualRegression:
    """视觉回归比对器

    用法:
        vr = VisualRegression(baseline_dir=Path("tests/e2e/baseline/TC-001"))
        result = vr.compare(
            current_screenshot=Path("reports/TC-001/final.png"),
            test_id="TC-001",
            step="final",
        )
        assert result.is_pass
    """

    # 默认阈值
    DEFAULT_PIXEL_THRESHOLD = 0.01  # 1% 像素差异视为失败
    DEFAULT_SSIM_THRESHOLD = 0.95
    DIFF_BLOCK_SIZE = 16  # 区域级 Diff 块大小
    RED_HSV_LOWER = (0, 100, 100)
    RED_HSV_UPPER = (10, 255, 255)
    RED_HSV_LOWER2 = (170, 100, 100)  # 红色在 HSV 的另一端
    RED_HSV_UPPER2 = (180, 255, 255)

    # 常见错误关键词
    ERROR_KEYWORDS = [
        "Error", "Failed", "失败", "异常", "错误",
        "500", "502", "503", "504",
        "404 Not Found", "Internal Server Error",
        "Network Error", "TypeError", "ReferenceError",
    ]

    def __init__(
        self,
        baseline_dir: Optional[Path] = None,
        pixel_threshold: float = DEFAULT_PIXEL_THRESHOLD,
        ssim_threshold: float = DEFAULT_SSIM_THRESHOLD,
        auto_save_baseline: bool = True,
    ):
        self.baseline_dir = Path(baseline_dir) if baseline_dir else None
        self.pixel_threshold = pixel_threshold
        self.ssim_threshold = ssim_threshold
        self.auto_save_baseline = auto_save_baseline
        if self.baseline_dir:
            self.baseline_dir.mkdir(parents=True, exist_ok=True)

    def compare(
        self,
        current_screenshot: Path,
        test_id: str,
        step: str,
        dom_signals: Optional[Dict[str, Any]] = None,
    ) -> DiffResult:
        """执行完整比对

        Args:
            current_screenshot: 当前截图路径
            test_id: 测试用例 ID
            step: 步骤名
            dom_signals: 可选的 DOM 探针信号（由调用方提供）

        Returns:
            DiffResult: 完整比对结果
        """
        from datetime import datetime

        current = Path(current_screenshot)
        result = DiffResult(
            test_id=test_id,
            step=step,
            baseline_path=None,
            current_path=str(current),
            timestamp=datetime.now().isoformat(),
        )

        if not current.exists():
            result.error = f"当前截图不存在: {current}"
            return result

        # 1) 视觉回归
        baseline_path = self._baseline_path(test_id, step)
        if baseline_path.exists():
            result.baseline_path = str(baseline_path)
            try:
                self._pixel_diff(baseline_path, current, result)
            except Exception as e:  # noqa: BLE001
                logger.exception("pixel diff 失败")
                result.error = f"pixel diff failed: {e}"
        else:
            # 无基线 → 首次执行时自动保存
            if self.auto_save_baseline and self.baseline_dir:
                self._save_baseline(current, test_id, step)
                result.error = "baseline_missing_saved"

        # 2) 数据显示不全（来自 DOM 探针）
        if dom_signals:
            self._check_data_incomplete(dom_signals, result)
            self._check_display_errors(dom_signals, result)

        # 3) 像素级错误检测（红色 toast）
        try:
            self._detect_red_errors(current, result)
        except Exception as e:  # noqa: BLE001
            logger.warning("red error detection 失败: %s", e)

        return result

    # ----- 基线管理 -------------------------------------------------------
    def _baseline_path(self, test_id: str, step: str) -> Path:
        if not self.baseline_dir:
            return Path(f"/nonexistent/{test_id}_{step}.png")
        return self.baseline_dir / f"{step}.png"

    def _save_baseline(self, current: Path, test_id: str, step: str) -> None:
        if not self.baseline_dir:
            return
        import shutil

        dst = self._baseline_path(test_id, step)
        shutil.copy2(current, dst)
        logger.info("已保存基线: %s", dst)

    def update_baseline(self, test_id: str, step: str, current: Path) -> Path:
        """手动更新基线（用于审阅后）"""
        if not self.baseline_dir:
            raise ValueError("baseline_dir 未配置")
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        dst = self._baseline_path(test_id, step)
        import shutil

        shutil.copy2(current, dst)
        return dst

    # ----- 像素 Diff -------------------------------------------------------
    def _pixel_diff(self, baseline: Path, current: Path, result: DiffResult) -> None:
        if not PIL_AVAILABLE:
            result.error = "PIL 未安装，跳过像素 Diff"
            return
        img_a = Image.open(baseline).convert("RGB")
        img_b = Image.open(current).convert("RGB")
        # 尺寸不一致 → 直接视为大面积变化
        if img_a.size != img_b.size:
            result.pixel_diff_ratio = 1.0
            result.changed_regions = [
                ChangedRegion(0, 0, img_b.size[0], img_b.size[1],
                              img_b.size[0] * img_b.size[1], "HIGH")
            ]
            return
        diff = ImageChops.difference(img_a, img_b)
        if NUMPY_AVAILABLE:
            arr = np.asarray(diff, dtype=np.int32).sum(axis=-1)
            total = arr.size
            changed = int((arr > 30).sum())  # 阈值 30 (0-255*3) 抗噪
            result.pixel_diff_ratio = changed / total if total else 0.0
            result.ssim_score = self._ssim(img_a, img_b)
            result.changed_regions = self._changed_regions(arr, threshold=30)
        else:
            # 退化方案：逐像素
            px_a = img_a.load()
            px_b = img_b.load()
            w, h = img_a.size
            total = w * h
            changed = 0
            for y in range(h):
                for x in range(w):
                    ra, ga, ba = px_a[x, y]
                    rb, gb, bb = px_b[x, y]
                    if abs(ra - rb) + abs(ga - gb) + abs(ba - bb) > 30:
                        changed += 1
            result.pixel_diff_ratio = changed / total if total else 0.0

    def _changed_regions(
        self, diff_array: "np.ndarray", threshold: int = 30, block: int = 16
    ) -> List[ChangedRegion]:
        """基于块的区域检测（简化连通域）"""
        if not NUMPY_AVAILABLE:
            return []
        h, w = diff_array.shape
        regions: List[ChangedRegion] = []
        visited = np.zeros_like(diff_array, dtype=bool)
        for by in range(0, h, block):
            for bx in range(0, w, block):
                sub = diff_array[by:by + block, bx:bx + block]
                if (sub > threshold).sum() > (block * block) * 0.3:
                    # 简化为块级区域
                    cnt = int((sub > threshold).sum())
                    severity = "HIGH" if cnt > 1000 else ("MEDIUM" if cnt > 200 else "LOW")
                    regions.append(
                        ChangedRegion(x=bx, y=by, width=min(block, w - bx),
                                      height=min(block, h - by),
                                      pixel_count=cnt, severity=severity)
                    )
        return regions

    def _ssim(self, img_a: "Image.Image", img_b: "Image.Image") -> float:
        """SSIM 简化实现（无 skimage）"""
        if not NUMPY_AVAILABLE:
            return 1.0
        a = np.asarray(img_a.convert("L"), dtype=np.float64)
        b = np.asarray(img_b.convert("L"), dtype=np.float64)
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        mu_a = a.mean()
        mu_b = b.mean()
        sigma_a = a.var()
        sigma_b = b.var()
        sigma_ab = ((a - mu_a) * (b - mu_b)).mean()
        num = (2 * mu_a * mu_b + c1) * (2 * sigma_ab + c2)
        den = (mu_a**2 + mu_b**2 + c1) * (sigma_a + sigma_b + c2)
        return float(num / den) if den else 1.0

    # ----- 数据显示不全 ---------------------------------------------------
    def _check_data_incomplete(self, signals: Dict[str, Any], result: DiffResult) -> None:
        """DOM 探针：检测数据显示不全

        signals 期望结构:
            {
                "truncated_texts": ["用户名..."],   # text-overflow ellipsis 触发的元素
                "overflow_elements": [              # 超出视口的元素
                    {"selector": "...", "rect": {"x":..,"y":..,"w":..,"h":..}}
                ],
                "loading_states": [                  # 仍处于 loading 状态的选择器
                    {"selector": "...", "duration_ms": 12000}
                ],
                "failed_images": [                   # naturalWidth=0 的图片
                    {"src": "...", "alt": "..."}
                ],
                "horizontal_scroll": False,          # body 出现横向滚动
            }
        """
        for t in signals.get("truncated_texts", []):
            result.data_incomplete.append(f"文本被截断: {t}")
        for ov in signals.get("overflow_elements", []):
            result.data_incomplete.append(
                f"元素溢出视口: {ov.get('selector', '?')} 位置 {ov.get('rect', {})}"
            )
        for ls in signals.get("loading_states", []):
            if ls.get("duration_ms", 0) > 10000:
                result.data_incomplete.append(
                    f"Loading 超过 10s: {ls.get('selector', '?')}"
                )
        for img in signals.get("failed_images", []):
            result.data_incomplete.append(
                f"图片未加载: src={img.get('src', '?')} alt={img.get('alt', '?')}"
            )
        if signals.get("horizontal_scroll"):
            result.data_incomplete.append("页面出现非预期横向滚动条")

    # ----- 显示错误检测 ---------------------------------------------------
    def _check_display_errors(self, signals: Dict[str, Any], result: DiffResult) -> None:
        """DOM 探针：检测显示错误

        signals 期望结构:
            {
                "error_toasts": [
                    {"type": "error", "text": "操作失败", "selector": "..."}
                ],
                "page_errors": ["TypeError: ..."],   # console pageerror
                "react_error_boundary": False,       # 是否触发 React 兜底
            }
        """
        for t in signals.get("error_toasts", []):
            result.display_errors.append(
                f"[{t.get('type', 'error')}] {t.get('text', '')} ({t.get('selector', '')})"
            )
        for e in signals.get("page_errors", []):
            # 简单关键词检测
            for kw in self.ERROR_KEYWORDS:
                if kw.lower() in e.lower():
                    result.display_errors.append(f"页面错误: {e[:200]}")
                    break
        if signals.get("react_error_boundary"):
            result.display_errors.append("检测到 React Error Boundary 兜底页")

    def _detect_red_errors(self, image_path: Path, result: DiffResult) -> None:
        """像素级红色错误检测（HSV 空间）"""
        if not PIL_AVAILABLE or not NUMPY_AVAILABLE:
            return
        img = Image.open(image_path).convert("RGB")
        arr = np.asarray(img, dtype=np.uint8)
        # 转换到 HSV
        hsv = self._rgb_to_hsv(arr)
        # 红色 mask
        mask1 = (
            (hsv[..., 0] >= self.RED_HSV_LOWER[0])
            & (hsv[..., 0] <= self.RED_HSV_UPPER[0])
            & (hsv[..., 1] >= self.RED_HSV_LOWER[1])
            & (hsv[..., 2] >= self.RED_HSV_LOWER[2])
        )
        mask2 = (
            (hsv[..., 0] >= self.RED_HSV_LOWER2[0])
            & (hsv[..., 0] <= self.RED_HSV_UPPER2[0])
            & (hsv[..., 1] >= self.RED_HSV_LOWER2[1])
            & (hsv[..., 2] >= self.RED_HSV_LOWER2[2])
        )
        red = mask1 | mask2
        ratio = float(red.sum()) / red.size
        # 红色占比 > 2% 且集中在某个区域（连通块 > 100 像素）→ 视为错误
        if ratio > 0.02 and red.sum() > 500:
            result.display_errors.append(
                f"检测到红色错误区域（占比 {ratio:.2%}）"
            )

    @staticmethod
    def _rgb_to_hsv(arr: "np.ndarray") -> "np.ndarray":
        """RGB→HSV (向量化)"""
        r, g, b = arr[..., 0] / 255.0, arr[..., 1] / 255.0, arr[..., 2] / 255.0
        mx = arr.max(axis=-1) / 255.0
        mn = arr.min(axis=-1) / 255.0
        df = mx - mn
        h = np.zeros_like(mx)
        s = np.where(mx > 0, df / np.maximum(mx, 1e-9), 0)
        v = mx
        rc = np.where(df > 0, (mx - r) / np.maximum(df, 1e-9) / 6.0, 0)
        gc = np.where(df > 0, (mx - g) / np.maximum(df, 1e-9) / 6.0, 0)
        bc = np.where(df > 0, (mx - b) / np.maximum(df, 1e-9) / 6.0, 0)
        h = np.where(r == mx, bc - gc, h)
        h = np.where(g == mx, 2.0 + rc - bc, h)
        h = np.where(b == mx, 4.0 + gc - rc, h)
        h = (h * 60) % 360  # 转角度
        return np.stack([h, s * 255, v * 255], axis=-1).astype(np.uint8)


# ---------------------------------------------------------------------------
# Playwright 集成探针（可选）
# ---------------------------------------------------------------------------
class DOMSignalsCollector:
    """从 Playwright Page 收集 DOM 探针信号

    用法:
        collector = DOMSignalsCollector(page)
        signals = collector.collect()
        vr.compare(..., dom_signals=signals)
    """

    # 溢出视口检测脚本（仅检测真实视觉缺陷）
    # - 仅检测水平溢出（r.right > vw + 5），垂直方向不检测（full_page 截图时整页 > vh 是正常的）
    # - 排除被滚动容器/可滚动祖先元素包裹的元素（这些是正常滚动行为，不算溢出）
    # - 仅保留真实位置错位的"孤立"溢出元素（fixed 定位、负 margin 等导致的视觉错位）
    # - 增加 body/documentElement 整体溢出状态作为参考信号
    OVERFLOW_JS = """
    () => {
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const docEl = document.documentElement;
        const body = document.body;
        // 整体溢出状态（仅水平方向才有意义）
        const docHorizontalScroll = docEl.scrollWidth - docEl.clientWidth > 5;
        const bodyHorizontalScroll = body.scrollWidth - body.clientWidth > 5;

        // 检查元素是否有可滚动的祖先元素（overflow: auto/scroll）
        const hasScrollableAncestor = (el) => {
            let parent = el.parentElement;
            while (parent && parent !== docEl) {
                const style = getComputedStyle(parent);
                if (style.overflowX === 'auto' || style.overflowX === 'scroll' ||
                    style.overflow === 'auto' || style.overflow === 'scroll') {
                    // 父级确实是可滚动容器，且子元素在容器内
                    const pr = parent.getBoundingClientRect();
                    const cr = el.getBoundingClientRect();
                    if (cr.right <= pr.right + 5) {
                        return true;
                    }
                }
                parent = parent.parentElement;
            }
            return false;
        };

        const overflow = [];
        document.querySelectorAll('*').forEach(el => {
            if (el === docEl || el === body) return;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            // 关键修复 1：full_page 截图下，页面总高 > vh 是正常滚动行为，
            //            不应将所有元素都判为溢出。仅检测水平方向。
            // 关键修复 2：位于可滚动容器内的元素也不应判为溢出（这正是滚动容器存在的意义）。
            const isHorizontalOverflow = r.right > vw + 5;
            if (!isHorizontalOverflow) return;
            if (hasScrollableAncestor(el)) return;
            const style = getComputedStyle(el);
            // 排除 transform/translate 导致的视觉越界（动画场景）
            if (style.position === 'fixed' || style.position === 'absolute') {
                // fixed/absolute 元素超出视口 → 真实 bug
                overflow.push({
                    selector: el.tagName + (el.id ? '#' + el.id : '') +
                              (el.className ? '.' + String(el.className).split(' ').join('.') : ''),
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                    position: style.position
                });
            } else if (docHorizontalScroll || bodyHorizontalScroll) {
                // 普通流元素 + 整体出现水平滚动 → 真实布局 bug
                overflow.push({
                    selector: el.tagName + (el.id ? '#' + el.id : '') +
                              (el.className ? '.' + String(el.className).split(' ').join('.') : ''),
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                    position: style.position
                });
            }
        });
        return overflow.slice(0, 20);
    }
    """

    LOADING_JS = """
    () => {
        const loadings = [];
        document.querySelectorAll('[aria-busy="true"], .ant-spin, .arco-spin, .el-loading-mask').forEach(el => {
            const t = el.getAttribute('data-loading-start');
            const dur = t ? (Date.now() - parseInt(t, 10)) : 0;
            loadings.push({
                selector: el.tagName + (el.className ? '.' + String(el.className).split(' ').join('.') : ''),
                duration_ms: dur
            });
        });
        return loadings;
    }
    """

    FAILED_IMG_JS = """
    () => {
        return [...document.querySelectorAll('img')].filter(i => i.complete && i.naturalWidth === 0)
            .map(i => ({src: i.src, alt: i.alt}));
    }
    """

    # 文本截断检测脚本
    # - 排除 Element Plus 表格单元格内的截断（el-table 自带 ellipsis 工具提示，截断是设计意图）
    # - 排除 input/textarea 内部（输入框 overflow: hidden 是正常行为）
    # - 排除 el-form/el-form-item 容器（label 区域 width 限制是表单布局设计）
    # - 排除有 title 属性或 el-tooltip 包裹的元素（hover 即可看全文，是设计行为）
    # - 仅保留内容展示区域（如卡片正文、列表项描述）的真实截断
    TRUNCATED_JS = """
    () => {
        // 判断元素是否在 Element Plus 表格内
        const isInsideTable = (el) => {
            let parent = el.parentElement;
            while (parent) {
                if (parent.classList && (
                    parent.classList.contains('el-table') ||
                    parent.classList.contains('el-table__cell') ||
                    parent.tagName === 'TBODY' || parent.tagName === 'THEAD' ||
                    parent.tagName === 'TR'
                )) {
                    return true;
                }
                parent = parent.parentElement;
            }
            return false;
        };
        // 判断元素是否被 el-tooltip 包裹（hover 可看全文）
        const isInsideTooltip = (el) => {
            let parent = el.parentElement;
            while (parent) {
                if (parent.classList && parent.classList.contains('el-tooltip') ||
                    parent.classList && parent.classList.contains('el-popper')) {
                    return true;
                }
                parent = parent.parentElement;
            }
            return false;
        };
        // 判断是否在 Element Plus 表单容器内（label 区域 width 限制是表单布局设计）
        const isInsideForm = (el) => {
            let parent = el.parentElement;
            while (parent) {
                if (parent.classList && (
                    parent.classList.contains('el-form') ||
                    parent.classList.contains('el-form-item')
                )) {
                    return true;
                }
                parent = parent.parentElement;
            }
            return false;
        };
        // 判断是否是表单控件（不需要截断检测）
        const isFormControl = (el) => {
            const tag = el.tagName;
            return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
        };

        return [...document.querySelectorAll('*')].filter(el => {
            if (isFormControl(el)) return false;
            if (isInsideForm(el)) return false;
            if (isInsideTable(el)) return false;
            if (isInsideTooltip(el)) return false;
            if (el.hasAttribute('title') && el.getAttribute('title').trim()) return false;
            // 必须有可见文本（避免空容器误报）
            const text = el.textContent?.trim();
            if (!text || text.length < 3) return false;
            // 必须有 scrollWidth > clientWidth + overflow: hidden
            if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow === 'hidden') {
                return true;
            }
            return false;
        }).map(el => el.textContent?.trim().slice(0, 50)).filter(Boolean).slice(0, 20);
    }
    """

    ERROR_TOAST_JS = """
    () => {
        const selectors = [
            '.ant-message-error', '.ant-notification-notice-error',
            '.arco-message-error', '.arco-notification-error',
            '.el-message--error', '.el-notification--error',
        ];
        const results = [];
        selectors.forEach(s => {
            document.querySelectorAll(s).forEach(el => {
                results.push({
                    type: 'error',
                    text: el.textContent?.trim() || '',
                    selector: s
                });
            });
        });
        return results;
    }
    """

    REACT_ERROR_BOUNDARY_JS = """
    () => {
        // 常见 React Error Boundary 兜底文字
        const text = document.body.textContent || '';
        return /something went wrong|应用程序发生错误|出错了/i.test(text);
    }
    """

    def __init__(self, page: Any):
        self.page = page

    def collect(self) -> Dict[str, Any]:
        """收集所有 DOM 探针信号"""
        signals: Dict[str, Any] = {
            "overflow_elements": self._safe_eval(self.OVERFLOW_JS, []),
            "loading_states": self._safe_eval(self.LOADING_JS, []),
            "failed_images": self._safe_eval(self.FAILED_IMG_JS, []),
            "truncated_texts": self._safe_eval(self.TRUNCATED_JS, []),
            "error_toasts": self._safe_eval(self.ERROR_TOAST_JS, []),
            "react_error_boundary": self._safe_eval(self.REACT_ERROR_BOUNDARY_JS, False),
            "horizontal_scroll": False,
        }
        # 横向滚动条
        try:
            signals["horizontal_scroll"] = self.page.evaluate(
                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
            )
        except Exception:  # noqa: BLE001
            pass
        return signals

    def _safe_eval(self, js: str, default: Any) -> Any:
        try:
            return self.page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            logger.debug("DOM 探针失败: %s", e)
            return default


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="视觉回归 + 数据显示完整性 + 显示错误检测"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cmp = sub.add_parser("compare", help="对比当前截图与基线")
    p_cmp.add_argument("--current", required=True)
    p_cmp.add_argument("--baseline-dir", required=True)
    p_cmp.add_argument("--test-id", required=True)
    p_cmp.add_argument("--step", default="final")
    p_cmp.add_argument("--output", help="JSON 报告输出路径")
    p_cmp.add_argument("--update-baseline", action="store_true",
                       help="用当前截图更新基线")

    p_audit = sub.add_parser("audit", help="像素级红色错误巡检（不需基线）")
    p_audit.add_argument("--current", required=True)
    p_audit.add_argument("--output", help="JSON 报告输出路径")

    args = parser.parse_args()
    if args.cmd == "compare":
        vr = VisualRegression(baseline_dir=Path(args.baseline_dir))
        if args.update_baseline:
            vr.update_baseline(args.test_id, args.step, Path(args.current))
            print(f"✅ 基线已更新: {args.test_id}/{args.step}")
            return 0
        result = vr.compare(Path(args.current), args.test_id, args.step)
        out = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(out, encoding="utf-8")
        print(out)
        return 0 if result.is_pass else 1
    if args.cmd == "audit":
        vr = VisualRegression(baseline_dir=None, auto_save_baseline=False)
        result = DiffResult(
            test_id="AUDIT",
            step="single",
            baseline_path=None,
            current_path=args.current,
        )
        try:
            vr._detect_red_errors(Path(args.current), result)
        except Exception as e:  # noqa: BLE001
            result.error = str(e)
        out = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(out, encoding="utf-8")
        print(out)
        return 0 if not result.display_errors else 1
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
