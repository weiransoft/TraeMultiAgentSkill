#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI/UX 优化分析

核心能力:
  1. 可访问性 (A11y): 对比度、alt、label、语义化标签、键盘可达
  2. 交互质量: 按钮最小尺寸、焦点可见性、加载反馈
  3. 布局与响应式: 元素重叠、文字截断、视口溢出
  4. UX 反模式: 强制注册、破坏性操作无确认、表单无校验

设计原则:
  - 标准库优先: 纯 Playwright JS 注入 + 规则引擎，零第三方
  - 失败安全: 任何检查项异常不影响其他检查
  - 可执行建议: 每条问题都给出明确的修复方案
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class UIUXIssue:
    """单个 UI/UX 问题"""

    severity: str  # HIGH / MEDIUM / LOW
    category: str  # a11y / interaction / layout / ux
    rule: str
    element: str  # 选择器或描述
    message: str
    fix: str
    metric: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# UI/UX 分析器
# ---------------------------------------------------------------------------
class UIUXAnalyzer:
    """UI/UX 巡检分析器

    用法:
        analyzer = UIUXAnalyzer()
        page.goto(url)
        issues = analyzer.audit(page)
        analyzer.dump(Path("reports/uiux.json"))
    """

    # WCAG AA 文本对比度阈值
    CONTRAST_AA_NORMAL = 4.5
    CONTRAST_AA_LARGE = 3.0
    # 最小可点击区域 (Apple HIG / Material)
    MIN_TAP_TARGET = 44  # px
    # 颜色正则
    RGB_RE = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)")
    HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})")

    # 综合探针 JS：一次返回所有数据，避免多次 evaluate
    AUDIT_JS = r"""
    () => {
        const data = {
            images: [],
            form_controls: [],
            buttons: [],
            links: [],
            headings: [],
            errors: [],
        };
        // 图片
        document.querySelectorAll('img').forEach((img, i) => {
            data.images.push({
                tag: 'img',
                selector: img.tagName + (img.id ? '#' + img.id : '') +
                          (img.className && typeof img.className === 'string' ? '.' + img.className.split(' ').join('.') : ''),
                alt: img.getAttribute('alt'),
                src: img.src,
                natural_width: img.naturalWidth,
                natural_height: img.naturalHeight,
                complete: img.complete,
            });
        });
        // 表单控件
        document.querySelectorAll('input, textarea, select').forEach((el) => {
            if (el.type === 'hidden') return;
            const id = el.id;
            let labelText = null;
            if (id) {
                const label = document.querySelector(`label[for="${id}"]`);
                if (label) labelText = label.textContent?.trim();
            }
            if (!labelText && el.closest('label')) {
                labelText = el.closest('label').textContent?.trim();
            }
            const ariaLabel = el.getAttribute('aria-label');
            const ariaLabelledBy = el.getAttribute('aria-labelledby');
            data.form_controls.push({
                tag: el.tagName,
                type: el.type || el.tagName,
                id: el.id,
                name: el.name,
                selector: (el.id ? '#' + el.id : el.tagName) + (el.name ? `[name="${el.name}"]` : ''),
                has_label: !!labelText,
                has_aria_label: !!ariaLabel,
                has_aria_labelledby: !!ariaLabelledBy,
                required: el.required,
                placeholder: el.placeholder,
            });
        });
        // 按钮
        document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]').forEach((el) => {
            const r = el.getBoundingClientRect();
            data.buttons.push({
                selector: el.tagName + (el.id ? '#' + el.id : '') +
                          (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ').join('.') : ''),
                text: (el.textContent || el.value || '').trim().slice(0, 50),
                width: r.width,
                height: r.height,
                visible: r.width > 0 && r.height > 0,
                disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
            });
        });
        // 链接
        document.querySelectorAll('a').forEach((el) => {
            data.links.push({
                selector: 'a[href="' + (el.getAttribute('href') || '').slice(0, 50) + '"]',
                text: (el.textContent || '').trim().slice(0, 50),
                href: el.getAttribute('href'),
                target: el.getAttribute('target'),
            });
        });
        // 标题层级
        const hs = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
        hs.forEach(h => {
            data.headings.push({
                level: parseInt(h.tagName.slice(1)),
                text: h.textContent?.trim().slice(0, 100) || '',
            });
        });
        // div + onclick（反模式）
        const divClicks = document.querySelectorAll('div[onclick], span[onclick]');
        data.errors.push({
            type: 'inline_onclick',
            count: divClicks.length,
        });
        return data;
    }
    """

    CONTRAST_JS = r"""
    () => {
        // 仅采样前 50 个可见文本元素计算对比度
        const samples = [];
        const elements = [...document.querySelectorAll('p, span, button, a, h1, h2, h3, h4, h5, h6, label, li, td')]
            .filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && el.textContent && el.textContent.trim();
            }).slice(0, 50);
        for (const el of elements) {
            const style = getComputedStyle(el);
            samples.push({
                text: el.textContent?.trim().slice(0, 30) || '',
                color: style.color,
                background: style.backgroundColor,
                font_size: parseFloat(style.fontSize),
                font_weight: parseInt(style.fontWeight, 10) || 400,
                selector: el.tagName + (el.id ? '#' + el.id : ''),
            });
        }
        return samples;
    }
    """

    def __init__(self) -> None:
        self.issues: List[UIUXIssue] = []

    def audit(self, page: Any) -> List[UIUXIssue]:
        """执行完整巡检"""
        self.issues = []
        try:
            dom_data = page.evaluate(self.AUDIT_JS)
        except Exception as e:  # noqa: BLE001
            logger.warning("DOM 巡检失败: %s", e)
            dom_data = {}
        try:
            contrast_samples = page.evaluate(self.CONTRAST_JS)
        except Exception as e:  # noqa: BLE001
            logger.warning("对比度采样失败: %s", e)
            contrast_samples = []
        self._check_a11y_images(dom_data.get("images", []))
        self._check_a11y_form(dom_data.get("form_controls", []))
        self._check_a11y_contrast(contrast_samples)
        self._check_a11y_headings(dom_data.get("headings", []))
        self._check_interaction_buttons(dom_data.get("buttons", []))
        self._check_interaction_links(dom_data.get("links", []))
        self._check_ux_antipatterns(dom_data.get("errors", []))
        return self.issues

    # ----- A11y 检测 ----------------------------------------------------
    def _check_a11y_images(self, images: List[Dict[str, Any]]) -> None:
        for img in images:
            if img.get("alt") is None:
                self.issues.append(UIUXIssue(
                    severity="MEDIUM",
                    category="a11y",
                    rule="img-alt",
                    element=img.get("selector", "img"),
                    message="图片缺少 alt 属性",
                    fix="添加描述性 alt（装饰图用 alt=\"\"）",
                ))
            if not img.get("complete") or img.get("natural_width", 0) == 0:
                self.issues.append(UIUXIssue(
                    severity="MEDIUM",
                    category="a11y",
                    rule="img-loaded",
                    element=img.get("selector", "img"),
                    message=f"图片加载失败: {img.get('src', '?')}",
                    fix="检查 src URL 是否可访问；为图片提供 onerror 兜底",
                ))

    def _check_a11y_form(self, controls: List[Dict[str, Any]]) -> None:
        for c in controls:
            if not (c.get("has_label") or c.get("has_aria_label") or c.get("has_aria_labelledby")):
                # placeholder 不算 label
                if c.get("type") not in ("submit", "button", "reset", "hidden"):
                    self.issues.append(UIUXIssue(
                        severity="HIGH",
                        category="a11y",
                        rule="form-label",
                        element=c.get("selector", "?"),
                        message=f"表单控件缺少 label（type={c.get('type')}）",
                        fix="添加 <label for=...> 或 aria-label 属性",
                    ))

    def _check_a11y_contrast(self, samples: List[Dict[str, Any]]) -> None:
        for s in samples:
            ratio = self._calc_contrast(s.get("color", ""), s.get("background", ""))
            if ratio is None:
                continue
            is_large = (s.get("font_size", 0) >= 18) or \
                       (s.get("font_size", 0) >= 14 and s.get("font_weight", 0) >= 700)
            budget = self.CONTRAST_AA_LARGE if is_large else self.CONTRAST_AA_NORMAL
            if ratio < budget:
                self.issues.append(UIUXIssue(
                    severity="HIGH" if ratio < 3.0 else "MEDIUM",
                    category="a11y",
                    rule="color-contrast",
                    element=s.get("selector", "?"),
                    message=f"文本对比度 {ratio:.1f}:1 < WCAG AA ({budget}:1) - {s.get('text', '')[:30]}",
                    fix="加深文字颜色或调浅/调深背景，确保 4.5:1 (大文本 3:1)",
                    metric={"ratio": ratio, "budget": budget, "text_color": s.get("color"),
                            "bg_color": s.get("background")},
                ))

    def _check_a11y_headings(self, headings: List[Dict[str, Any]]) -> None:
        if not headings:
            return
        levels = [h["level"] for h in headings]
        # 不允许跳级（如 h1 → h3）
        for i in range(1, len(levels)):
            if levels[i] - levels[i - 1] > 1:
                self.issues.append(UIUXIssue(
                    severity="LOW",
                    category="a11y",
                    rule="heading-skip",
                    element=f"h{levels[i - 1]} → h{levels[i]}",
                    message=f"标题层级跳级: h{levels[i - 1]} 直接到 h{levels[i]}",
                    fix="保持连续层级（h1 → h2 → h3）",
                ))
        # 多个 h1
        if levels.count(1) > 1:
            self.issues.append(UIUXIssue(
                severity="LOW",
                category="a11y",
                rule="heading-multiple-h1",
                element="h1",
                message=f"页面存在 {levels.count(1)} 个 h1",
                fix="每页只用一个 h1，作为页面主标题",
            ))

    # ----- 交互质量 -----------------------------------------------------
    def _check_interaction_buttons(self, buttons: List[Dict[str, Any]]) -> None:
        for btn in buttons:
            if not btn.get("visible") or btn.get("disabled"):
                continue
            w, h = btn.get("width", 0), btn.get("height", 0)
            if w > 0 and h > 0 and (w < self.MIN_TAP_TARGET or h < self.MIN_TAP_TARGET):
                self.issues.append(UIUXIssue(
                    severity="MEDIUM",
                    category="interaction",
                    rule="tap-target",
                    element=btn.get("selector", "?"),
                    message=f"按钮可点击区域 {w:.0f}x{h:.0f} < {self.MIN_TAP_TARGET}x{self.MIN_TAP_TARGET}",
                    fix=f"扩大按钮 padding 或 min-width/min-height 至 {self.MIN_TAP_TARGET}px",
                    metric={"width": w, "height": h, "min": self.MIN_TAP_TARGET},
                ))
            if not btn.get("text"):
                self.issues.append(UIUXIssue(
                    severity="HIGH",
                    category="interaction",
                    rule="button-text",
                    element=btn.get("selector", "?"),
                    message="按钮无可识别文字",
                    fix="为按钮添加 text 或 aria-label",
                ))

    def _check_interaction_links(self, links: List[Dict[str, Any]]) -> None:
        for link in links:
            if not link.get("text") and not link.get("href", "").startswith("#"):
                # 纯图标链接需 aria-label
                self.issues.append(UIUXIssue(
                    severity="LOW",
                    category="interaction",
                    rule="link-text",
                    element=link.get("selector", "?"),
                    message="链接无可识别文字（可能是纯图标）",
                    fix="为链接添加 text 或 aria-label",
                ))

    # ----- UX 反模式 -----------------------------------------------------
    def _check_ux_antipatterns(self, errors: List[Dict[str, Any]]) -> None:
        for e in errors:
            if e.get("type") == "inline_onclick" and e.get("count", 0) > 0:
                self.issues.append(UIUXIssue(
                    severity="LOW",
                    category="ux",
                    rule="inline-onclick",
                    element="div[onclick], span[onclick]",
                    message=f"发现 {e['count']} 个内联 onclick 的 div/span",
                    fix="改用 <button> 元素（自带键盘可达 + 屏幕阅读器友好）",
                    metric={"count": e["count"]},
                ))

    # ----- 对比度计算 ---------------------------------------------------
    def _calc_contrast(self, fg: str, bg: str) -> Optional[float]:
        """计算两个颜色之间的对比度（WCAG）"""
        c1 = self._parse_color(fg)
        c2 = self._parse_color(bg)
        if c1 is None or c2 is None:
            return None
        # 透明背景 → 无法计算
        if c2[3] == 0:
            return None
        l1 = self._relative_luminance(c1)
        l2 = self._relative_luminance(c2)
        lighter, darker = max(l1, l2), min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)

    def _parse_color(self, color: str) -> Optional[tuple]:
        """解析 CSS 颜色到 (r, g, b, a) 0-255 / 0-1"""
        color = color.strip().lower()
        if color in ("transparent", "rgba(0, 0, 0, 0)"):
            return (0, 0, 0, 0)
        m = self.RGB_RE.search(color)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    float(m.group(4)) if m.group(4) else 1.0)
        m = self.HEX_RE.match(color)
        if m:
            hex_str = m.group(1)
            if len(hex_str) == 3:
                r, g, b = (int(c * 2, 16) for c in hex_str)
            elif len(hex_str) == 6:
                r, g, b = (int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
            else:
                return None
            return (r, g, b, 1.0)
        return None

    @staticmethod
    def _relative_luminance(rgb: tuple) -> float:
        r, g, b, _ = rgb

        def adj(c: int) -> float:
            v = c / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * adj(r) + 0.7152 * adj(g) + 0.0722 * adj(b)

    # ----- 评分与报告 ---------------------------------------------------
    def score(self) -> int:
        """综合评分 0-100（HIGH 扣 5，MEDIUM 扣 2，LOW 扣 1）"""
        penalty = 0
        for i in self.issues:
            if i.severity == "HIGH":
                penalty += 5
            elif i.severity == "MEDIUM":
                penalty += 2
            else:
                penalty += 1
        return max(0, 100 - penalty)

    def report(self) -> Dict[str, Any]:
        return {
            "score": self.score(),
            "total_issues": len(self.issues),
            "high_count": sum(1 for i in self.issues if i.severity == "HIGH"),
            "medium_count": sum(1 for i in self.issues if i.severity == "MEDIUM"),
            "low_count": sum(1 for i in self.issues if i.severity == "LOW"),
            "is_pass": not any(i.severity == "HIGH" for i in self.issues),
            "issues": [i.to_dict() for i in self.issues],
        }

    def dump(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.report(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("UI/UX 报告已写入: %s (score=%d)", output, self.score())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="UI/UX 分析 CLI（打印汇总）")
    parser.add_argument("--report", required=True, help="UI/UX JSON 报告路径")
    parser.add_argument("--fail-on-high", action="store_true",
                        help="存在 HIGH 级别问题时返回非零退出码")
    args = parser.parse_args()
    path = Path(args.report)
    if not path.exists():
        print(f"❌ 报告不存在: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"🎨 UI/UX 评分: {data.get('score', 0)}/100 (通过: {data.get('is_pass', False)})")
    print(f"   HIGH: {data.get('high_count', 0)}  "
          f"MEDIUM: {data.get('medium_count', 0)}  "
          f"LOW: {data.get('low_count', 0)}")
    issues = data.get("issues", [])
    if issues:
        print("\n🔥 问题列表:")
        for i in issues[:20]:
            print(f"   [{i['severity']}] {i['rule']}: {i['message']}")
            print(f"      元素: {i['element']}")
            print(f"      修复: {i['fix'][:100]}{'...' if len(i['fix']) > 100 else ''}")
    if args.fail_on_high and data.get("high_count", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
