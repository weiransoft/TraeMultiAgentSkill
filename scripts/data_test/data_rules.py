#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.13 业务数据规则确定性求值器（浏览器测试闭环数据层内核之一）。

职责（纯标准库、零 I/O、零第三方依赖）：
  1. ``eval_expr``      —— 受限安全表达式求值：自研 tokenizer + 递归下降，
     **绝不使用 eval()**。支持字面量 / 点路径变量 / 比较 / 布尔 / 算术 /
     ``in`` / ``is null`` / ``matches``（正则）/ 白名单函数 len·abs·coalesce。
  2. ``diff_snapshots`` —— before/after 快照行级差异（added/removed/changed，
     changed 精确到列值），供 DC 契约对照。
  3. ``evaluate_rules`` —— 五类业务规则（UNIQ/INV/CONS/SM/REF）确定性求值，
     输出 expected/actual/violations 明细，recorder 直接转成断言落盘。

设计红线：
  - 表达式文法封闭：未知字符、未知标识符、未知函数在词法/语法层立即拒绝，
    不存在代码执行面；null 参与有序比较、0==False 类隐式转换一律显式报错；
  - 规则求值失败（表达式错、谓词无命中、行数不符）都是"规则 FAIL"并带
    中文明细，绝不静默吞错。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "RuleEvalError",
    "eval_expr",
    "diff_snapshots",
    "diff_matches_expectation",
    "evaluate_rules",
    "VALID_RULE_TYPES",
]


class RuleEvalError(ValueError):
    """受限表达式/规则求值的统一错误类型（中文消息，可直接进断言 actual）。"""


# ---------------------------------------------------------------------------
# 一、受限安全表达式求值器
# ---------------------------------------------------------------------------
# 文法（优先级从低到高）：
#   or_expr    := and_expr ( 'or' and_expr )*
#   and_expr   := not_expr ( 'and' not_expr )*
#   not_expr   := 'not' not_expr | cmp_expr
#   cmp_expr   := arith ( ('=='|'!='|'>='|'<='|'>'|'<'|'in'|'matches'|'is' ...) arith )?
#   arith      := term ( ('+'|'-'|'*'|'/'|'%') term )*
#   term       := '-' term | atom
#   atom       := NUMBER | STRING | 'true'|'false'|'null'
#               | IDENT ('.' IDENT)*            # 点路径变量，从上下文取值
#               | IDENT '(' args ')'            # 白名单函数 len/abs/coalesce
#               | '(' or_expr ')' | '[' items ']'

# 词法：多字符运算符在前，杜绝 '>' 抢先吃掉 '>='
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+\.\d+|\d+)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>==|!=|>=|<=|&&|\|\||[-+*/%()<>\[\].,!])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)

# 关键字 → 内部 token 归一
_KEYWORDS = {
    "and": "and", "or": "or", "not": "not",
    "true": "bool", "false": "bool", "null": "null", "none": "null",
    "in": "in", "matches": "matches", "is": "is",
}

# 白名单函数表（封闭集合，新增函数必须在此登记并配单测）
_ALLOWED_FUNCS = {"len", "abs", "coalesce"}


def _tokenize(expr: str) -> List[Tuple[str, Any]]:
    """把表达式文本切分为 (类型, 值) token 序列；未知字符立即拒绝。"""
    tokens: List[Tuple[str, Any]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise RuleEvalError(
                "表达式含非法字符 {0!r}（位置 {1}）：仅允许字面量/变量/比较/"
                "算术/布尔/in/matches 与白名单函数".format(expr[pos], pos))
        pos = m.end()
        kind = m.lastgroup
        text = m.group()
        if kind == "ws":
            continue
        if kind == "num":
            tokens.append(("num", float(text) if "." in text else int(text)))
        elif kind == "str":
            # 去引号 + 还原转义（\' \" \\ \n）
            inner = text[1:-1]
            inner = re.sub(
                r"\\(.)", lambda mm: {"n": "\n"}.get(mm.group(1), mm.group(1)), inner)
            tokens.append(("str", inner))
        elif kind == "op":
            norm = {"&&": "and", "||": "or"}.get(text, text)
            tokens.append(("op", norm))
        else:  # ident
            kw = _KEYWORDS.get(text.lower())
            if kw == "bool":
                tokens.append(("bool", text.lower() == "true"))
            elif kw == "null":
                tokens.append(("null", None))
            elif kw in ("and", "or", "not", "in", "matches", "is"):
                tokens.append(("op", kw))
            else:
                tokens.append(("ident", text))
    return tokens


class _Parser:
    """递归下降解析 + 即时求值合一（变量上下文在构造时注入）。"""

    def __init__(self, tokens: List[Tuple[str, Any]], ctx: Dict[str, Any]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._ctx = ctx

    def _peek(self) -> Optional[Tuple[str, Any]]:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> Tuple[str, Any]:
        tok = self._peek()
        if tok is None:
            raise RuleEvalError("表达式意外结束")
        self._pos += 1
        return tok

    def _accept_op(self, op: str) -> bool:
        tok = self._peek()
        if tok and tok[0] == "op" and tok[1] == op:
            self._pos += 1
            return True
        return False

    def parse(self) -> Any:
        value = self._or_expr()
        if self._peek() is not None:
            raise RuleEvalError("表达式存在未消费的残留 token：{0!r}".format(self._peek()))
        return value

    def _or_expr(self) -> Any:
        left = self._and_expr()
        while self._accept_op("or"):
            right = self._and_expr()
            left = _truthy(left) or _truthy(right)
        return left

    def _and_expr(self) -> Any:
        left = self._not_expr()
        while self._accept_op("and"):
            right = self._not_expr()
            left = _truthy(left) and _truthy(right)
        return left

    def _not_expr(self) -> Any:
        if self._accept_op("not"):
            return not _truthy(self._not_expr())
        return self._cmp_expr()

    def _cmp_expr(self) -> Any:
        left = self._arith()
        tok = self._peek()
        if tok and tok[0] == "op" and tok[1] in (
                "==", "!=", ">", "<", ">=", "<=", "in", "matches", "is"):
            self._pos += 1
            op = tok[1]
            if op == "is":
                # is null / is not null 两种形态
                if self._accept_op("not"):
                    right = self._atom()
                    return not (left is None and right is None)
                right = self._atom()
                return (left is None) and (right is None)
            if op == "in":
                # in 右侧按设计只允许列表/字符串：'(' 开头时手工收集逗号分隔项
                # 成列表（('a','b') 的圆括号若交给 _atom 会被当带括号表达式，
                # 逗号成残留 token）；其余形态（[...] 字面量/字符串变量）走 _atom
                if self._accept_op("("):
                    items_in: List[Any] = [self._or_expr()]
                    while self._accept_op(","):
                        items_in.append(self._or_expr())
                    if not self._accept_op(")"):
                        raise RuleEvalError("in 集合缺少 )")
                    right: Any = items_in
                else:
                    right = self._atom()
            else:
                right = self._arith()
            return _compare(op, left, right)
        return left

    def _arith(self) -> Any:
        left = self._term()
        while True:
            tok = self._peek()
            if tok and tok[0] == "op" and tok[1] in ("+", "-", "*", "/", "%"):
                self._pos += 1
                right = self._term()
                left = _arith_op(tok[1], left, right)
            else:
                return left

    def _term(self) -> Any:
        if self._accept_op("-"):
            val = self._term()
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise RuleEvalError("一元负号仅适用于数值，实际：{0!r}".format(val))
            return -val
        return self._atom()

    def _atom(self) -> Any:
        tok = self._next()
        kind, val = tok
        if kind in ("num", "str", "bool", "null"):
            return val
        if kind == "op" and val == "(":
            inner = self._or_expr()
            if not self._accept_op(")"):
                raise RuleEvalError("缺少右括号 )")
            return inner
        if kind == "op" and val == "[":
            items: List[Any] = []
            if not self._accept_op("]"):
                items.append(self._or_expr())
                while self._accept_op(","):
                    items.append(self._or_expr())
                if not self._accept_op("]"):
                    raise RuleEvalError("列表字面量缺少 ]")
            return items
        if kind == "ident":
            if self._accept_op("("):
                # 函数调用：封闭白名单
                if val not in _ALLOWED_FUNCS:
                    raise RuleEvalError("函数 {0!r} 不在白名单 {1} 内".format(
                        val, sorted(_ALLOWED_FUNCS)))
                args: List[Any] = []
                if not self._accept_op(")"):
                    args.append(self._or_expr())
                    while self._accept_op(","):
                        args.append(self._or_expr())
                    if not self._accept_op(")"):
                        raise RuleEvalError("函数调用缺少 )")
                return _call_func(val, args)
            # 点路径变量取值：a.b.c
            cur: Any = self._ctx
            path = [val]
            while self._accept_op("."):
                tk = self._next()
                if tk[0] != "ident":
                    raise RuleEvalError("点路径后必须跟标识符，实际 {0!r}".format(tk))
                path.append(tk[1])
            for seg in path:
                if isinstance(cur, dict) and seg in cur:
                    cur = cur[seg]
                else:
                    raise RuleEvalError("上下文缺少变量 {0!r}（路径 {1}）".format(
                        seg, ".".join(path)))
            return cur
        raise RuleEvalError("意外的 token：{0!r}".format(tok))


def _truthy(v: Any) -> bool:
    """布尔判定（null → False，其余按 Python 真值语义）。"""
    return bool(v)


def _strict_eq(left: Any, right: Any) -> bool:
    """严格相等：数值跨 int/float 可比；其余必须同型（防 0==False 类陷阱）。"""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _compare(op: str, left: Any, right: Any) -> bool:
    """比较运算（严格类型域；null 参与有序比较直接报错）。"""
    if op == "in":
        if not isinstance(right, (list, tuple, str)):
            raise RuleEvalError("in 右侧必须是列表或字符串，实际 {0}".format(
                type(right).__name__))
        return left in right
    if op == "matches":
        if not isinstance(left, str) or not isinstance(right, str):
            raise RuleEvalError("matches 两侧必须是字符串（左=值，右=正则）")
        try:
            return re.search(right, left) is not None
        except re.error as exc:
            raise RuleEvalError("正则非法：{0}".format(exc)) from exc
    if op == "==":
        return _strict_eq(left, right)
    if op == "!=":
        return not _strict_eq(left, right)
    for side, name in ((left, "左"), (right, "右")):
        if side is None:
            raise RuleEvalError("{0}操作数为 null，不能参与 {1} 有序比较".format(name, op))
    if isinstance(left, bool) or isinstance(right, bool):
        raise RuleEvalError("布尔值不能参与 {0} 有序比较".format(op))
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        if not (isinstance(left, str) and isinstance(right, str)):
            raise RuleEvalError("{0} 比较要求两侧同为数值或同为字符串".format(op))
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    return left <= right


def _arith_op(op: str, left: Any, right: Any) -> Any:
    """算术运算（仅数值域；除零直接报错——错误即断言失败，绝不静默）。"""
    for side, name in ((left, "左"), (right, "右")):
        if not isinstance(side, (int, float)) or isinstance(side, bool):
            raise RuleEvalError("{0}操作数非数值：{1!r}".format(name, side))
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            raise RuleEvalError("表达式除数为零")
        return left / right
    if right == 0:
        raise RuleEvalError("表达式取模除数为零")
    return left % right


def _call_func(name: str, args: List[Any]) -> Any:
    """白名单函数实现（与 _ALLOWED_FUNCS 一一对应的封闭集合）。"""
    if name == "len":
        if len(args) != 1:
            raise RuleEvalError("len() 接受 1 个参数")
        if not isinstance(args[0], (str, list, tuple, dict)):
            raise RuleEvalError("len() 仅适用于字符串/列表/字典")
        return len(args[0])
    if name == "abs":
        if len(args) != 1 or not isinstance(args[0], (int, float)) or isinstance(args[0], bool):
            raise RuleEvalError("abs() 接受 1 个数值参数")
        return abs(args[0])
    if name == "coalesce":
        for a in args:
            if a is not None:
                return a
        return None
    raise RuleEvalError("未知函数 {0}".format(name))  # pragma: no cover


def eval_expr(expr: str, ctx: Optional[Dict[str, Any]] = None) -> Any:
    """求值受限安全表达式（本模块对外主入口之一）。

    Args:
        expr: 表达式文本（文法见模块 docstring）。
        ctx: 变量上下文（快照行字典扁平传入，键为列名/别名；支持 a.b 点路径）。

    Returns:
        Any: 比较/布尔类为 bool，算术类为数值。

    Raises:
        RuleEvalError: 词法非法、文法错误、未知变量/函数、类型域错误、除零。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise RuleEvalError("空表达式")
    tokens = _tokenize(expr)
    return _Parser(tokens, dict(ctx or {})).parse()


# ---------------------------------------------------------------------------
# 二、快照行级 diff
# ---------------------------------------------------------------------------

def _row_key(row: Dict[str, Any], key_cols: Sequence[str]) -> Tuple:
    """按 key 列构造行主键元组；key 列为空时退化为整行规范化形态（集合差异语义）。"""
    if key_cols:
        return tuple(row.get(c) for c in key_cols)
    return tuple(sorted((str(k), str(v)) for k, v in row.items()))


def diff_snapshots(
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
    key_cols: Sequence[str] = (),
) -> Dict[str, Any]:
    """两份快照（行字典列表）做行级差异对照。

    Returns:
        dict: ``{key_columns, before_count, after_count, added, removed,
        changed: [{key, before_row, changed_columns: [{column, before, after}]}]}``，
        changed 精确到列级新旧值，可直接渲染为断言明细。
    """
    before_map = {_row_key(r, key_cols): r for r in before_rows}
    after_map = {_row_key(r, key_cols): r for r in after_rows}

    added = [after_map[k] for k in sorted(after_map.keys(), key=str) if k not in before_map]
    removed = [before_map[k] for k in sorted(before_map.keys(), key=str) if k not in after_map]
    changed: List[Dict[str, Any]] = []
    for key in sorted(set(before_map) & set(after_map), key=str):
        b_row, a_row = before_map[key], after_map[key]
        cols = sorted(set(b_row) | set(a_row))
        col_diffs = [
            {"column": c, "before": b_row.get(c), "after": a_row.get(c)}
            for c in cols
            if not _strict_eq(b_row.get(c), a_row.get(c))
        ]
        if col_diffs:
            # before_row 随带：diff_matches_expectation 合并出变更后全行形态
            changed.append({"key": [str(x) for x in key],
                            "before_row": dict(b_row),
                            "changed_columns": col_diffs})
    return {
        "key_columns": list(key_cols),
        "before_count": len(before_rows),
        "after_count": len(after_rows),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def diff_matches_expectation(
    diff: Dict[str, Any], expectation: Dict[str, Any]
) -> Tuple[bool, str]:
    """把行级 diff 与 DC 契约的 DB 预期做确定性对照。

    预期结构（全部可选，缺省即不校验该项）::

        {"row_delta": 1, "added_count": 1, "removed_count": 0, "changed_count": 0,
         "expected_columns": {"status": "paid"}}

    Returns:
        tuple[bool, str]: (是否一致, 不一致明细——直接进断言 actual)。
    """
    problems: List[str] = []
    if "row_delta" in expectation:
        actual_delta = diff["after_count"] - diff["before_count"]
        if actual_delta != expectation["row_delta"]:
            problems.append("行数增量预期 {0}，实际 {1}".format(
                expectation["row_delta"], actual_delta))
    for field, key in (("added_count", "added"), ("removed_count", "removed"),
                       ("changed_count", "changed")):
        if field in expectation and len(diff[key]) != expectation[field]:
            problems.append("{0} 预期 {1} 行，实际 {2} 行".format(
                field.split("_")[0], expectation[field], len(diff[key])))
    expected_cols = expectation.get("expected_columns") or {}
    if expected_cols:
        hit_rows: List[Dict[str, Any]] = list(diff["added"])
        for ch in diff["changed"]:
            merged = dict(ch.get("before_row", {}))
            for cd in ch["changed_columns"]:
                merged[cd["column"]] = cd["after"]
            hit_rows.append(merged)
        matched = any(
            all(_strict_eq(row.get(c), v) for c, v in expected_cols.items())
            for row in hit_rows
        )
        if not matched:
            problems.append("命中行中无一行满足预期列值 {0}（候选行 {1} 条）".format(
                expected_cols, len(hit_rows)))
    if problems:
        return False, "；".join(problems)
    return True, "与 DB 预期逐项吻合"


# ---------------------------------------------------------------------------
# 三、业务规则求值（DR-*）
# ---------------------------------------------------------------------------

VALID_RULE_TYPES = {"UNIQ", "INV", "CONS", "SM", "REF"}

# 各规则类型的必填字段（结构校验前置，缺字段是配置错误立即拒绝）
_RULE_REQUIRED_FIELDS = {
    "UNIQ": ("snapshot", "column"),
    "INV": ("snapshot", "expr"),
    "CONS": ("expr", "vars"),
    "SM": ("before_snapshot", "after_snapshot", "column", "transitions"),
    "REF": ("child_snapshot", "child_column", "parent_snapshot", "parent_column"),
}


def _rows_of(snapshots: Dict[str, Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """取快照行（快照缺失是规则结构错误，立即拒绝而不是当作空集）。"""
    if name not in snapshots:
        raise RuleEvalError("快照 {0!r} 不存在（可用：{1}）".format(name, sorted(snapshots)))
    rows = snapshots[name].get("rows")
    if not isinstance(rows, list):
        raise RuleEvalError("快照 {0!r} 缺少 rows 数组".format(name))
    return rows


def _eval_uniq(rule: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """UNIQ：指定列值在快照行内唯一（NULL 不参与唯一性——SQL 标准语义）。"""
    rows = _rows_of(snapshots, rule["snapshot"])
    col = rule["column"]
    seen: Dict[Any, int] = {}
    for row in rows:
        val = row.get(col)
        if val is None:
            continue
        seen[val] = seen.get(val, 0) + 1
    dup = {str(k): c for k, c in seen.items() if c > 1}
    return {
        "pass": not dup,
        "expected": "列 {0} 值唯一".format(col),
        "actual": "无重复" if not dup else "重复值 {0} 个：{1}".format(len(dup), list(dup)[:20]),
        "violations": [{"column": col, "value": k, "count": c}
                       for k, c in list(dup.items())[:20]],
    }


def _eval_inv(rule: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """INV：不变式表达式对每一行都成立（任一行失败/求值错 → 规则失败）。"""
    rows = _rows_of(snapshots, rule["snapshot"])
    expr = rule["expr"]
    violations: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        try:
            ok = _truthy(eval_expr(expr, row))
        except RuleEvalError as exc:
            violations.append({"row": idx, "error": str(exc)})
            continue
        if not ok:
            violations.append({"row": idx, "row_data": row})
    return {
        "pass": not violations,
        "expected": "全部 {0} 行满足 {1!r}".format(len(rows), expr),
        "actual": "全部满足" if not violations else "违例 {0} 行".format(len(violations)),
        "violations": violations[:20],
    }


def _eval_cons(rule: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """CONS：守恒等式——vars 把表达式变量绑定到 快照.列。

    vars 值形态：``"snapshot.column"``（快照须恰好 1 行）或
    ``"snapshot[列=值].column"``（按谓词取第一命中行；无命中 → 规则失败）。
    """
    bindings: Dict[str, Any] = {}
    for var, ref in (rule.get("vars") or {}).items():
        snap_part, _, col = str(ref).rpartition(".")
        if "[" in snap_part:
            snap_name, predicate = snap_part.split("[", 1)
            predicate = predicate.rstrip("]")
            pcol, _, pval = predicate.partition("=")
            rows = [r for r in _rows_of(snapshots, snap_name) if str(r.get(pcol)) == pval]
            if not rows:
                return {"pass": False,
                        "expected": "{0} 存在 {1}={2} 的行".format(snap_name, pcol, pval),
                        "actual": "谓词无命中行", "violations": []}
            row = rows[0]
        else:
            rows = _rows_of(snapshots, snap_part)
            if len(rows) != 1:
                return {"pass": False,
                        "expected": "{0} 恰好 1 行（守恒基准）".format(snap_part),
                        "actual": "实际 {0} 行".format(len(rows)), "violations": []}
            row = rows[0]
        bindings[var] = row.get(col)
    expr = rule["expr"]
    ok = _truthy(eval_expr(expr, bindings))
    return {
        "pass": ok,
        "expected": expr,
        "actual": "成立（变量 {0}）".format(bindings) if ok
        else "不成立（变量 {0}）".format(bindings),
        "violations": [] if ok else [{"vars": bindings}],
    }


def _eval_sm(rule: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """SM：状态机——按 key 列配对 before/after 行，迁移必须 ∈ 合法迁移表。"""
    col = rule["column"]
    key_cols = rule.get("key_columns") or []
    transitions = {tuple(t) for t in rule.get("transitions") or []}
    allow_same = bool(rule.get("allow_same", True))
    before_rows = _rows_of(snapshots, rule["before_snapshot"])
    after_rows = _rows_of(snapshots, rule["after_snapshot"])
    after_map = {_row_key(r, key_cols): r for r in after_rows}
    violations: List[Dict[str, Any]] = []
    checked = 0
    for br in before_rows:
        key = _row_key(br, key_cols)
        ar = after_map.get(key)
        if ar is None:
            continue  # 行消失归 diff/REF 判定面，SM 只管存续行的状态迁移
        checked += 1
        frm, to = br.get(col), ar.get(col)
        if frm == to and allow_same:
            continue
        if (frm, to) not in transitions:
            violations.append({"key": [str(x) for x in key], "from": frm, "to": to})
    return {
        "pass": not violations,
        "expected": "状态列 {0} 迁移 ∈ {1}{2}".format(
            col, sorted(map(list, transitions)),
            "（允许保持不变）" if allow_same else ""),
        "actual": "{0} 对状态全部合法".format(checked) if not violations
        else "非法迁移 {0} 条".format(len(violations)),
        "violations": violations[:20],
    }


def _eval_ref(rule: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """REF：引用完整性——子快照列值必须命中父快照列值集合（NULL 豁免）。"""
    child = _rows_of(snapshots, rule["child_snapshot"])
    parent = _rows_of(snapshots, rule["parent_snapshot"])
    ccol, pcol = rule["child_column"], rule["parent_column"]
    parent_vals = {r.get(pcol) for r in parent}
    violations = [
        {"row_index": i, ccol: row.get(ccol)}
        for i, row in enumerate(child)
        if row.get(ccol) is not None and row.get(ccol) not in parent_vals
    ]
    return {
        "pass": not violations,
        "expected": "{0}.{1} 全部命中 {2}.{3}".format(
            rule["child_snapshot"], ccol, rule["parent_snapshot"], pcol),
        "actual": "全部 {0} 行引用有效".format(len(child)) if not violations
        else "悬空引用 {0} 行".format(len(violations)),
        "violations": violations[:20],
    }


_EVALUATORS = {
    "UNIQ": _eval_uniq,
    "INV": _eval_inv,
    "CONS": _eval_cons,
    "SM": _eval_sm,
    "REF": _eval_ref,
}


def evaluate_rules(
    rules: Sequence[Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """对声明式规则清单逐条确定性求值。

    Args:
        rules: 规则声明数组（data-rules.json 的 rules），每条含 rule_id/type
            及类型专属参数（见各 _eval_* docstring）。
        snapshots: 快照名 → ``{"rows": [{col: val}, ...]}``。

    Returns:
        list[dict]: 每条 ``{rule_id, type, pass, expected, actual,
        violations[:20]}``——违规明细截前 20 条防报告爆炸，计数不截。

    Raises:
        RuleEvalError: 规则结构非法 / 快照缺失（配置错误，不是数据失败）。
    """
    results: List[Dict[str, Any]] = []
    for rule in rules:
        rid = rule.get("rule_id") or "<no-id>"
        rtype = rule.get("type")
        if rtype not in VALID_RULE_TYPES:
            raise RuleEvalError("规则 {0}: type {1!r} 非法（合法 {2}）".format(
                rid, rtype, sorted(VALID_RULE_TYPES)))
        for required in _RULE_REQUIRED_FIELDS[rtype]:
            if required not in rule:
                raise RuleEvalError("规则 {0}（{1}）缺必填字段 {2!r}".format(
                    rid, rtype, required))
        try:
            verdict = _EVALUATORS[rtype](rule, snapshots)
        except RuleEvalError:
            raise
        except Exception as exc:  # 意外异常也转为规则失败（不吞、不假通过）
            verdict = {"pass": False,
                       "expected": "规则正常求值",
                       "actual": "求值异常：{0}".format(exc),
                       "violations": []}
        verdict.update({"rule_id": rid, "type": rtype})
        results.append(verdict)
    return results
