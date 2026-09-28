"""动作分级器（纯函数）——红线⑤"危险按钮零点击"的判定内核。

对应架构 ARCH-SU-001 §2.3.6 ``classify_action``（2026-09-28 审查收窄后的判定链）
与 PRD REQ-SU-006（AC1/AC3/AC4）。本模块**不 import playwright**：输入是
DOM 剪枝产物 :class:`ElementSignature`（由 site_crawler.extract_signature 在
浏览器侧提取），输出是可直接落 ``page_actions`` 表的 :class:`ActionDecision`。

拆分为独立文件（相对架构清单的合理微调）理由同 url_key：分级器是**纯函数**，
PRD REQ-SU-006 AC1 要求"给定 fixture 逐条断言 T1/T2/T3"，独立模块可脱离
浏览器完成全部规则链单测（``test_su_action_tier``）。

判定链（顺序即优先级，与本次交付任务书的五步口径一一对应，
并兼容架构 §2.3.6 的完整判定面——危险动词/上传/登出是危险检查的
扩充面，插在 ①/② 之后、T2 之前，不改变任务书五步的先后关系）：
  ① 显式 ``<form method=post>``（非 GET）        → T3（规则名：表单方法非GET）
  ② href 非同域（含伪协议 javascript:/mailto:）  → T3（规则名：非同域链接；
     blocked 由 route 层做，分级器只判级——非同域 <a> 不进入 T1）
  ③ 元素文本 / aria-label 命中 DANGER_VERBS      → T3（规则名：命中危险动词）
  ④ 文件上传控件 / 登出特征                       → T3（规则名：危险控件）
  ⑤ T2 仅当"显式 ``<form method=GET>`` 且 form_text 不含 DANGER_VERBS"
     （规则名：显式GET表单；显式 GET 但 form 内含危险动词 → T3，
      规则名：GET表单含危险动词——PRD REQ-SU-006 AC1 明列用例）
  ⑥ 同域 ``<a href>`` 或 GET 导航                → T1（规则名：同域链接）
  ⑦ 其余一律 T3（规则名：默认拒绝，fail-safe；含 SAFE_VERBS 裸按钮，
     命中的安全动词写入 rule_name 后缀供文档解释）

**rule_name 中文化（本批交付口径）**：全部规则名为中文可解释文本
（落 ``page_actions.rule_name``，NFR-SU-008），文档据此解释
"为什么没点这个按钮"，无需查代码即可读懂。

**SAFE_VERBS 的用途约束（REQ-SU-006 AC4，2026-09-28 审查硬性口径）**：
SAFE_VERBS **只**用于给已判 T3 的记录补充"预估只读语义"的解释信息
（``ActionDecision.semantic_hint``），**绝不作为任何执行/点击依据**。
裸按钮即使文本命中"搜索/查询/查看"也一律 T3——理由：
① method 由客户端自报，不可信；② 遗留系统的 GET 端点可能改状态；
③ 裸按钮的 JS 副作用不可撤销。可执行档位只认**结构条件**（T1 链接 / T2 显式 GET 表单）。
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

__all__ = [
    "TIER_T1",
    "TIER_T2",
    "TIER_T3",
    "DANGER_VERBS",
    "SAFE_VERBS",
    "ElementSignature",
    "ActionDecision",
    "classify_action",
    "fill_neutral",
    "neutral_value",
    "NEUTRAL_VALUE",
    "MAX_NEUTRAL_VALUE_LEN",
    "NEUTRAL_VALUE_PATTERN",
]

# 三档级别常量（page_actions.tier 列取值，与 §3 DDL CHECK 对齐）
TIER_T1 = "T1"   # 放行：同域链接 / GET 型导航
TIER_T2 = "T2"   # 只读表单提交：仅显式 GET 表单，可用中性值填充后执行
TIER_T3 = "T3"   # 危险操作：只记录，绝不点击（红线⑤）

# 危险动词黑名单（架构 §2.3.6 常量口径，中英文双语；命中即 T3）。
# 比较前统一小写 + 去空白，故英文条目一律小写；中文条目保持原样。
DANGER_VERBS: List[str] = [
    # 中文（PRD REQ-SU-006 原文黑名单）
    "删除", "提交", "发布", "审批", "支付", "重置", "导出全部", "清空", "停用", "启用",
    "新增", "新建", "创建", "修改", "编辑", "保存", "导入", "退款", "审核", "驳回",
    "下架", "上架", "冻结", "解冻", "绑定", "解绑", "授权", "撤销", "关闭", "开启",
    "转账", "充值", "提现", "签署", "作废", "归档", "复制", "移动", "批量",
    # 英文
    "delete", "submit", "publish", "approve", "pay", "reset", "export", "clear",
    "disable", "enable", "create", "insert", "update", "save", "import", "refund",
    "reject", "merge", "revert", "deploy", "terminate", "suspend",
    "grant", "revoke", "upload", "destroy", "remove", "add", "edit", "new",
    "logout", "signout", "sign out", "log out",
]

# 安全动词白名单（**仅用于 T3 记录的解释性语义标注，绝不作为执行依据**，AC4）。
# 审查理由（架构 §2.3.6）：SAFE_VERBS 命中只说明"该按钮*可能*是只读语义"，
# 但客户端 method 不可信、GET 端点可能改状态、裸按钮 JS 副作用不可撤销，
# 因此命中本表**不改变档位**，只写入 semantic_hint 供文档解释"预估语义"。
SAFE_VERBS: List[str] = [
    # 中文（PRD REQ-SU-006 原文白名单）
    "搜索", "查询", "筛选", "查看", "详情", "下一页",
    "上一页", "首页", "末页", "展开", "收起", "刷新", "排序", "预览", "帮助",
    # 英文
    "search", "query", "filter", "view", "detail", "details", "next", "prev",
    "previous", "first", "last", "expand", "collapse", "refresh", "sort",
    "preview", "help",
]

# 登出特征（href / name / id 片段，命中即 T3；与 DANGER_VERBS 的 logout 互补：
# 动词表覆盖"可见文本"，本表覆盖"文本为空但 id/URI 暴露登出语义"的图标按钮）
_LOGOUT_HINTS: List[str] = [
    "logout", "signout", "sign_out", "sign-out", "log_out", "log-out",
    "unauth", "退出", "登出", "注销", "安全退出",
]

# 非 GET 的表单 method（HTML 规范外的自定义值也视为非 GET，一律 T3）
_NON_GET_METHODS = frozenset({"post", "put", "patch", "delete"})

# T2 中性测试值（REQ-SU-006 AC3：长度 ≤20 且 ^[A-Za-z0-9]*$，由 neutral_value 保证）
NEUTRAL_VALUE = "test"
MAX_NEUTRAL_VALUE_LEN = 20
# 中性值合法形态断言基准（fill_neutral 返回值必须匹配，防止任何真实/危险值混入 T2 填表）
NEUTRAL_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9]{0,20}$")


@dataclass
class ElementSignature:
    """DOM 剪枝产物（架构 §2.3.6；不存全量 HTML，NFR-SU-005 单快照 ≤64KB）。

    **隐私硬约束**：本结构**永不含 input 的 value 字段**——extract_signature 在
    浏览器侧只读 name/type/placeholder/aria-label/文本等静态属性，用户已输入的
    敏感内容不得进入签名（§5.1 静态审查项）。
    """

    tag: str                                        # 标签名（小写，如 'a'/'button'/'input'）
    role: Optional[str] = None                      # ARIA role（如 'button'/'link'）
    text: Optional[str] = None                      # 可见文本（已 trim）
    aria_label: Optional[str] = None                # aria-label
    href: Optional[str] = None                      # <a>/<area> 的 href（可为相对）
    is_form_control: bool = False                   # 是否表单控件（input/select/textarea/button）
    form_method: Optional[str] = None               # 所属 form 的 method（小写，缺省 None）
    form_text: Optional[str] = None                 # 所属 form 的聚合文本（按钮/标签/字段名）
    selector: str = ""                              # 稳定 CSS 路径（T2 执行 / T3 记录用）
    input_type: Optional[str] = None                # <input type=...>（小写；file 触发 T3）
    name: Optional[str] = None                      # 控件 name 属性
    element_id: Optional[str] = None                # 元素 id 属性
    # 所属 form 的 CSS 路径（T2 去重键数据源；结构属性、零业务数据）。
    # 2026-09-28 e2e 场景[2]根因修复新增：同表单多控件共享同值，使
    # "一个表单一份预算"去重与 HTML 表单一一对应（旧按控件属性生成
    # 去重键的口径会把同表单拆成多份，详见 site_crawler._form_key_for）
    form_selector: Optional[str] = None
    # 所属 form 的显式 action 原始声明值（结构属性、零 value 面）。
    # 2026-09-28 e2e 场景[2] 40s 挂起根因修复新增：T2/T3 的 action 目标
    # 解析改走签名数据通道（site_crawler._form_action_for 第①级优先读
    # 本字段），悬挂导航期间不再逐控件发起文档级 DOM 调用
    form_action: Optional[str] = None
    # 控件自身显式 formaction（HTML5：控件级覆盖 form action；同为结构
    # 属性，优先级高于 form_action，与 DOM 语义一致）
    formaction: Optional[str] = None


@dataclass
class ActionDecision:
    """分级结果（落 ``page_actions`` 表：tier + rule_name 逐条可解释，NFR-SU-008）。

    Attributes:
        element: 被判级的元素签名。
        tier: 'T1'/'T2'/'T3'。
        rule_name: 命中的规则名（全部中文化可解释，如 '命中危险动词'/
            '显式GET表单'/'默认拒绝'），文档据此解释"为什么没点这个按钮"。
        matched_keyword: 命中的具体动词/特征串（无命中为 None），便于人工复核。
        semantic_hint: 解释性语义标注——T3 且文本命中 SAFE_VERBS 时写入
            'readable_semantic:<动词>'，**仅作记录，不构成执行依据**（AC4）。
    """

    element: ElementSignature
    tier: str
    rule_name: str
    matched_keyword: Optional[str] = None
    semantic_hint: Optional[str] = None


def _norm(text: Optional[str]) -> str:
    """文本归一：None→空串、小写、去除所有空白字符。

    去空白的目的：中文按钮常写成"删 除"、英文常量 ``"Log Out"`` 与表项
    ``"logout"`` 需能互相匹配上。
    """
    if not text:
        return ""
    return "".join(text.lower().split())


def _first_hit(haystack: str, keywords: List[str]) -> Optional[str]:
    """在已归一的 haystack 中查找首个命中的关键词（返回表内原文）。

    Args:
        haystack: 归一后的待匹配文本（小写、无空白）。
        keywords: 关键词表（原始形态）。

    Returns:
        str | None: 命中的关键词原文；无命中 None。
    """
    if not haystack:
        return None
    for keyword in keywords:
        needle = _norm(keyword)
        if needle and needle in haystack:
            return keyword
    return None


def _same_domain(href: str, base_url: Optional[str]) -> bool:
    """判定 href 是否与 base_url 同域（T1 的结构条件之一）。

    相对 href（无 scheme/host）天然同域（浏览器按 base_url 解析）；
    绝对 href 比较 hostname（小写）。无法解析的 href（javascript:、mailto: 等）
    判为不同域——这类伪协议链接的副作用不可预测，交给"非同域链接"/"默认拒绝"兜 T3。

    Args:
        href: 链接地址（原始形态）。
        base_url: 站点入口（``allowed_origins`` 的基准，可为 None）。

    Returns:
        bool: True=同域。
    """
    if not href:
        return False
    target = urlsplit(href)
    # 伪协议（javascript:/mailto:/data:/blob:）：scheme 存在但无 host → 拒绝
    if target.scheme and not target.netloc:
        return False
    if not target.netloc:
        # 无 host：相对链接（含 '//host' 以外的全部形态）→ 同域
        return True
    if not base_url:
        # 无基准可比：保守判不同域（fail-safe → "非同域链接"/"默认拒绝"）
        return False
    base_host = (urlsplit(base_url).hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if not base_host or not target_host:
        return False
    return target_host == base_host


def _form_method(form_ctx: Optional[Dict[str, Any]], el: ElementSignature) -> Optional[str]:
    """取生效的表单 method（小写）。

    优先级：form_ctx['method'] > 元素自带 form_method（元素属性优先于上下文快照，
    对应 HTML5 formaction/formmethod 语义）。

    Args:
        form_ctx: 表单上下文（extract_signature 产出，含 method/text 等键）。
        el: 元素签名。

    Returns:
        str | None: 归一小写的 method；两者皆缺返回 None。
    """
    if form_ctx:
        raw = form_ctx.get("method")
        if raw:
            return str(raw).strip().lower()
    if el.form_method:
        return el.form_method.strip().lower()
    return None


def _form_text(form_ctx: Optional[Dict[str, Any]], el: ElementSignature) -> str:
    """取所属 form 的聚合文本（按钮/标签/字段名），form_ctx 优先于元素快照。"""
    if form_ctx:
        raw = form_ctx.get("text")
        if raw:
            return str(raw)
    return el.form_text or ""


def classify_action(
    el: ElementSignature,
    form_ctx: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
) -> ActionDecision:
    """动作分级纯函数（红线⑤判定内核，REQ-SU-006 AC1/AC4）。

    判定链严格按 a→f 顺序执行，先命中先返回（危险优先于放行，纵深防御）。

    Args:
        el: 候选元素签名（DOM 剪枝产物，永不含 input value）。
        form_ctx: 可选表单上下文，识别键：``method``（表单 method）、
            ``text``（表单内按钮/标签/字段名聚合文本）。None 时退化为
            只读 ``el.form_method`` / ``el.form_text``。
        base_url: 站点入口，用于同域判定（默认 None=不放行任何绝对链接，
            保守落"非同域链接"/"默认拒绝"）。

    Returns:
        ActionDecision: tier + rule_name（+ 命中词/语义标注），逐条落库可解释。

    Note:
        本函数**无任何副作用**（不点击、不发请求、不写库），单测可纯 fixture 驱动。
    """
    method = _form_method(form_ctx, el)
    form_text_norm = _norm(_form_text(form_ctx, el))
    # 元素自身语义文本 = 可见文本 + aria-label 一并扫描（危险动词的扫描面；
    # 二者都可能是按钮语义的载体，图标按钮常只有 aria-label）
    el_text_norm = _norm(el.text) + " " + _norm(el.aria_label)
    el_text_norm = el_text_norm.strip()

    # ---- ① 表单 method 非 GET → T3（表单方法非GET）----
    # 显式 POST/PUT/PATCH/DELETE 直接拒；method 缺省（None）时 HTML 默认 GET，
    # 这里按"未声明"处理，交由 ⑤ 的"显式 GET"条件把关（缺省不算显式）。
    if method in _NON_GET_METHODS:
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="表单方法非GET",
            matched_keyword=method,
        )

    # ---- ② href 非同域 → T3（非同域链接）----
    # 任务书审查口径：blocked（route.abort）由 route 层做，**分级器只判级**——
    # 非同域 <a> 一律不给 T1（误给 T1 会让 crawler 主动 goto 外域）。
    # 仅对携带 href 的链接类元素判定；同域判定失败包含伪协议
    # （javascript:/mailto:/data:）与"无 base_url 可比"两种 fail-safe 场景。
    if el.tag in ("a", "area") and el.href and not _same_domain(el.href, base_url):
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="非同域链接",
            matched_keyword=el.href,
        )

    # ---- ③ 元素文本 / aria-label 命中危险动词 → T3（命中危险动词）----
    # 扫描面严格限定为**元素自身可见文本 + aria-label**（用户能看到的语义）。
    # 表单聚合文本 form_text 不在这里扫——否则"关键词输入框 + 删除按钮"这种
    # 同表单混排会让搜索框也被危险动词抢先拒掉，从而使 ⑤ 的
    # "显式 GET 但 form 内含危险动词 → T3"分支
    # （PRD REQ-SU-006 AC1 明列用例）永远不可达（2026-09-28 自测修正）。
    el_text_norm = _norm(el.text) or _norm(el.aria_label)
    danger_hit = _first_hit(el_text_norm, DANGER_VERBS)
    if danger_hit is not None:
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="命中危险动词",
            matched_keyword=danger_hit,
        )

    # ---- ④ 文件上传控件 / 登出特征 → T3（危险控件）----
    if (el.input_type or "").strip().lower() == "file":
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="危险控件",
            matched_keyword="file_upload",
        )
    # 登出特征扫描面：文本、aria-label、href、name、id（图标登出按钮常无文本）
    logout_haystack = " ".join(
        _norm(v) for v in (el.text, el.aria_label, el.href, el.name, el.element_id) if v
    )
    logout_hit = _first_hit(logout_haystack, _LOGOUT_HINTS)
    if logout_hit is not None:
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="危险控件",
            matched_keyword="logout:{0}".format(logout_hit),
        )

    # ---- ⑤ T2：仅"显式 GET 表单内的表单控件"（显式GET表单）----
    # 三个条件缺一不可：① 是表单控件；② method **显式**声明为 GET
    # （None=未声明，不满足"显式"口径，AC1 要求 <form method=post> 与缺省都不得进 T2）；
    # ③ form 聚合文本无危险动词（③ 已扫过元素文本，此处再扫 form 全文）。
    if el.is_form_control and method == "get":
        form_danger = _first_hit(form_text_norm, DANGER_VERBS)
        if form_danger is None:
            return ActionDecision(
                element=el,
                tier=TIER_T2,
                rule_name="显式GET表单",
            )
        # 显式 GET 但 form 内含危险动词 → T3（PRD AC1 明列该用例）
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="GET表单含危险动词",
            matched_keyword=form_danger,
        )

    # ---- ⑥ T1：同域 <a href> / GET 型导航（同域链接）----
    # 非同域 <a> 已在 ② 拦截，走到这里的绝对链接必为同域；相对 href 天然同域。
    if el.tag == "a" and el.href and _same_domain(el.href, base_url):
        return ActionDecision(
            element=el,
            tier=TIER_T1,
            rule_name="同域链接",
        )

    # ---- ⑦ 其余一律 T3（默认拒绝，默认拒绝原则 / fail-safe）----
    # SAFE_VERBS 命中在此**只补充解释性 rule_name/语义标注**，档位仍是 T3
    # （AC4 硬约束：命中安全动词不构成任何执行依据）。
    safe_hit = _first_hit(el_text_norm, SAFE_VERBS)
    if safe_hit is not None:
        # rule_name 记录命中的安全动词，供文档解释"预估只读语义但仍未点击"的原因
        return ActionDecision(
            element=el,
            tier=TIER_T3,
            rule_name="默认拒绝（命中安全动词：{0}，仍不执行）".format(safe_hit),
            matched_keyword=safe_hit,
            semantic_hint="readable_semantic:{0}".format(safe_hit),
        )
    return ActionDecision(
        element=el,
        tier=TIER_T3,
        rule_name="默认拒绝",
    )


def neutral_value() -> str:
    """T2 表单填充用的中性测试值（REQ-SU-006 AC3）。

    固定返回 ``"test"``：长度 4 ≤ 20、且满足 ``^[A-Za-z0-9]*$``——调用方
    （site_crawler.fill_neutral）以断言强制这两个约束，保证 T2 执行
    绝不把任何真实/危险值写进目标系统。

    Returns:
        str: 中性值 ``"test"``。
    """
    return NEUTRAL_VALUE


def fill_neutral(el: ElementSignature) -> str:
    """按控件类型返回 T2 填表中性值（REQ-SU-006 AC3，纯函数）。

    取值策略（任务书口径："test" 或空串）：
      - 文本类控件（text/search/tel/url/email/password/未声明 type 的 input、
        textarea）→ ``"test"``：显式 GET 查询表单需要一个可观测的非空值，
        便于 API 观测面看到查询参数的 shape；
      - 其余控件（number/date 系列/checkbox/radio/select 等）→ 空串 ``""``：
        这些控件对任意文本值会产生校验错误或无意义查询，空串是**最小副作用**
        取值（number 留空即"不加该过滤条件"，checkbox/radio 不勾选）；
      - **永不读取 el 的任何 value 字段**（ElementSignature 结构上也不存在
        value 字段——隐私硬约束，§5.1 静态审查项），返回值只由控件类型决定。

    返回值恒满足 ``^[A-Za-z0-9]{0,20}$``（两个候选值 'test'/'' 均满足，
    以 :data:`NEUTRAL_VALUE_PATTERN` 断言兜底，防未来改动引入非法形态）。

    Args:
        el: T2 表单控件签名。

    Returns:
        str: ``"test"`` 或 ``""``（调用方 site_crawler 直接用它 fill）。

    Raises:
        AssertionError: 返回值不满足中性值形态约束（防御性断言，
            当前实现不可能触发——两个取值均合法）。
    """
    input_type = (el.input_type or "").strip().lower()
    tag = (el.tag or "").strip().lower()
    # 文本类控件集合：input 未声明 type 时浏览器默认 text（HTML 规范）
    text_like = {
        "", "text", "search", "tel", "url", "email", "password",
    }
    if tag == "textarea" or (tag == "input" and input_type in text_like):
        value = NEUTRAL_VALUE
    else:
        value = ""
    # 形态断言（AC3 由断言保证）：任何返回必须匹配 ^[A-Za-z0-9]{0,20}$
    assert NEUTRAL_VALUE_PATTERN.match(value), \
        "fill_neutral 返回值违反中性值形态约束：{0!r}".format(value)
    return value
