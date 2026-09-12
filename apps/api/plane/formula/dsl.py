"""受限公式 DSL（TASK-014 §2.3，P4 R3）。

安全模型：**纯解析求值，绝不 eval/exec**——词法 → 递归下降 AST → 白名单
函数求值；标识符仅 prop/prop_cf/sub_*，无变量、无循环、无函数定义
（刻意非图灵完备，拒绝 ScriptRunner 式代码执行，§1.5）。

复杂度上限（BR-03）：AST 节点 ≤ 200、嵌套深度 ≤ 10、引用字段 ≤ 20。
类型系统：number / text / boolean / date / null 五型；隐式转换仅
number→text、date→text；其余不符即求值错误（BR-05 错误值显式不阻断）。

语法（EBNF 精简）：
    expr    := or
    or      := and ( ('or'|'||') and )*
    and     := cmp ( ('and'|'&&') cmp )*
    cmp     := add ( ('=='|'!='|'<'|'<='|'>'|'>=') add )?
    add     := mul ( ('+'|'-') mul )*
    mul     := unary ( ('*'|'/'|'%') unary )*
    unary   := '-' unary | primary
    primary := NUM | STR | 'true' | 'false' | '(' expr ')'
             | IDENT '(' args ')'          # 函数调用（白名单）
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable
from dataclasses import dataclass

#: 复杂度上限（BR-03）
MAX_NODES = 200
MAX_DEPTH = 10
MAX_REFS = 20


class FormulaError(Exception):
    """基类（保存/求值统一错误面）。"""


class FormulaSyntaxError(FormulaError):
    """语法非法。"""


class FormulaComplexityError(FormulaError):
    """超出复杂度上限（BR-03）。"""


class FormulaRuntimeError(FormulaError):
    """求值期错误（除零/类型不符/引用被删——BR-05 错误值显式）。"""


# ── 词法 ────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(\.\d+)?)
  | (?P<str>'([^'\\]|\\.)*')
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|\+|-|\*|/|%|\(|\)|,|<|>)
""",
    re.VERBOSE,
)


@dataclass
class Tok:
    kind: str
    text: str


def tokenize(src: str) -> list[Tok]:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if m is None:
            raise FormulaSyntaxError(f"非法字符 {src[pos]!r} @ {pos}")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        text = m.group()
        assert m.lastgroup is not None  # 正则具名组必命中
        if m.lastgroup == "str":
            text = text[1:-1].replace("\\'", "'")
        out.append(Tok(m.lastgroup, text))
    return out


#: 上下文函数（求值器内建分支处理，不在 FUNCTIONS 数值表）
_CONTEXT_FUNCTIONS = frozenset({"prop", "prop_cf", "sub_count", "sub_done_count", "sub_sum", "if"})


# ── AST ─────────────────────────────────────────────────────────────
# 节点形态（tuple）：("num", v) ("str", v) ("bool", v) ("neg", a)
#   ("op2", op, a, b) ("call", name, [args])

Node = tuple


class _Parser:
    def __init__(self, toks: list[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Tok:
        t = self.peek()
        if t is None:
            raise FormulaSyntaxError("表达式意外结束")
        self.i += 1
        return t

    def expect(self, text: str) -> None:
        t = self.next()
        if t.text != text:
            raise FormulaSyntaxError(f"期望 {text!r}，得到 {t.text!r}")

    def parse(self) -> Node:
        node = self.expr()
        rest = self.peek()
        if rest is not None:
            raise FormulaSyntaxError(f"多余记号 {rest.text!r}")
        return node

    def expr(self) -> Node:
        return self.or_()

    def or_(self) -> Node:
        left = self.and_()
        while (t := self.peek()) and t.text in ("or", "||"):
            self.next()
            left = ("op2", "or", left, self.and_())
        return left

    def and_(self) -> Node:
        left = self.cmp()
        while (t := self.peek()) and t.text in ("and", "&&"):
            self.next()
            left = ("op2", "and", left, self.cmp())
        return left

    def cmp(self) -> Node:
        left = self.add()
        if (t := self.peek()) and t.text in ("==", "!=", "<", "<=", ">", ">="):
            self.next()
            return ("op2", t.text, left, self.add())
        return left

    def add(self) -> Node:
        left = self.mul()
        while (t := self.peek()) and t.text in ("+", "-"):
            self.next()
            left = ("op2", t.text, left, self.mul())
        return left

    def mul(self) -> Node:
        left = self.unary()
        while (t := self.peek()) and t.text in ("*", "/", "%"):
            self.next()
            left = ("op2", t.text, left, self.unary())
        return left

    def unary(self) -> Node:
        if (t := self.peek()) and t.text == "-":
            self.next()
            return ("neg", self.unary())
        return self.primary()

    def primary(self) -> Node:
        t = self.next()
        if t.kind == "num":
            return ("num", float(t.text) if "." in t.text else int(t.text))
        if t.kind == "str":
            return ("str", t.text)
        if t.text in ("true", "false"):
            return ("bool", t.text == "true")
        if t.text == "(":
            node = self.expr()
            self.expect(")")
            return node
        if t.kind == "ident":
            self.expect("(")
            args = []
            if not ((p := self.peek()) and p.text == ")"):
                args.append(self.expr())
                while (p := self.peek()) and p.text == ",":
                    self.next()
                    args.append(self.expr())
            self.expect(")")
            return ("call", t.text, args)
        raise FormulaSyntaxError(f"意外记号 {t.text!r}")


def parse(src: str) -> Node:
    """解析 + 复杂度校验（BR-03）。"""
    if not src or not src.strip():
        raise FormulaSyntaxError("公式不能为空")
    node = _Parser(tokenize(src)).parse()
    nodes, depth, refs = _metrics(node)
    if nodes > MAX_NODES:
        raise FormulaComplexityError(f"AST 节点数 {nodes} 超上限 {MAX_NODES}")
    if depth > MAX_DEPTH:
        raise FormulaComplexityError(f"嵌套深度 {depth} 超上限 {MAX_DEPTH}")
    if refs > MAX_REFS:
        raise FormulaComplexityError(f"引用字段数 {refs} 超上限 {MAX_REFS}")
    return node


def _metrics(node: Node) -> tuple[int, int, int]:
    """(节点数, 深度, 引用数)。"""
    if node[0] in ("num", "str", "bool"):
        return 1, 1, 0
    if node[0] == "neg":
        n, d, r = _metrics(node[1])
        return n + 1, d + 1, r
    if node[0] == "op2":
        n1, d1, r1 = _metrics(node[2])
        n2, d2, r2 = _metrics(node[3])
        return n1 + n2 + 1, max(d1, d2) + 1, r1 + r2
    if node[0] == "call":
        name, args = node[1], node[2]
        if name not in FUNCTIONS and name not in _CONTEXT_FUNCTIONS:
            raise FormulaSyntaxError(f"函数 {name!r} 不在白名单")
        n = d = 1
        r = 1 if name in ("prop", "prop_cf", "sub_sum") else 0
        for a in args:
            if name in ("prop", "prop_cf") and a[0] != "str":
                raise FormulaSyntaxError(f"{name}() 参数必须是字符串字面量")
            an, ad, ar = _metrics(a)
            n += an
            d = max(d, ad + 1)
            r += ar
        return n, d, r
    raise FormulaSyntaxError(f"未知节点 {node[0]!r}")


def collect_refs(node: Node) -> set[str]:
    """引用的 cf_ 字段键集合（依赖图构建用）。"""
    if node[0] == "call" and node[1] == "prop_cf":
        return {node[2][0][1]}
    if node[0] == "neg":
        return collect_refs(node[1])
    if node[0] == "op2":
        return collect_refs(node[2]) | collect_refs(node[3])
    if node[0] == "call":
        refs: set[str] = set()
        for a in node[2]:
            refs |= collect_refs(a)
        return refs
    return set()


# ── 求值 ────────────────────────────────────────────────────────────


def _num(v, what="值"):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FormulaRuntimeError(f"{what} 期望 number，得到 {_typename(v)}")
    return v


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, _dt.date):
        return v.isoformat()
    return str(v)


def _typename(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, _dt.date):
        return "date"
    return "text"


def _to_date(v):
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str):
        try:
            return _dt.date.fromisoformat(v[:10])
        except ValueError:
            pass
    raise FormulaRuntimeError(f"期望 date，得到 {_typename(v)}")


FUNCTIONS: dict[str, Callable] = {}


def _fn(name):
    def deco(f):
        FUNCTIONS[name] = f
        return f

    return deco


@_fn("subtract")
def _subtract(a, b):
    return _num(a) - _num(b)


@_fn("round")
def _round(x, n=0):
    return round(float(_num(x)), int(_num(n)))


@_fn("abs")
def _abs(x):
    return abs(_num(x))


@_fn("if")
def _if(cond, then, else_=None):  # noqa: ARG001 —— 惰性在求值器内处理
    raise FormulaRuntimeError("if 由求值器短路求值")


@_fn("gt")
def _gt(a, b):
    return _num(a) > _num(b)


@_fn("gte")
def _gte(a, b):
    return _num(a) >= _num(b)


@_fn("lt")
def _lt(a, b):
    return _num(a) < _num(b)


@_fn("lte")
def _lte(a, b):
    return _num(a) <= _num(b)


@_fn("eq")
def _eq(a, b):
    return a == b


@_fn("ne")
def _ne(a, b):
    return a != b


@_fn("not")
def _not(a):
    if not isinstance(a, bool):
        raise FormulaRuntimeError(f"not 期望 boolean，得到 {_typename(a)}")
    return not a


@_fn("days_between")
def _days_between(a, b):
    return (_to_date(a) - _to_date(b)).days


@_fn("now")
def _now():
    return _dt.date.today()


@_fn("date_add")
def _date_add(d, n):
    return _to_date(d) + _dt.timedelta(days=int(_num(n)))


@_fn("concat")
def _concat(*parts):
    return "".join(_text(p) for p in parts)


@_fn("upper")
def _upper(s):
    return _text(s).upper()


@_fn("lower")
def _lower(s):
    return _text(s).lower()


@_fn("len")
def _len(s):
    v = _text(s)
    if not isinstance(s, str):
        raise FormulaRuntimeError(f"len 期望 text，得到 {_typename(s)}")
    return len(v)


@_fn("minutes")
def _minutes(n):
    return int(_num(n))


@_fn("hours")
def _hours(n):
    return int(_num(n) * 60)


@dataclass
class EvalContext:
    """求值上下文（宿主提供属性读取面）。"""

    props: dict  # prop('name') 源（任务内置属性）
    custom: dict  # prop_cf('cf_key') 源（custom_fields）
    sub_count: int = 0
    sub_done_count: int = 0
    sub_values: dict | None = None  # sub_sum 上下文（子任务属性列表）


def evaluate(node: Node, ctx: EvalContext):
    """白名单求值（类型不符/除零抛 FormulaRuntimeError——BR-05）。"""
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "neg":
        return -_num(evaluate(node[1], ctx))
    if kind == "op2":
        op = node[1]
        if op == "if":
            pass
        a = evaluate(node[2], ctx)
        b = evaluate(node[3], ctx)
        if op == "or":
            return (a is True) or (b is True)
        if op == "and":
            return (a is True) and (b is True)
        if op in ("==", "!="):
            eq = _loose_eq(a, b)
            return eq if op == "==" else not eq
        if op in ("<", "<=", ">", ">="):
            if isinstance(a, _dt.date) or isinstance(b, _dt.date):
                a, b = _to_date(a), _to_date(b)
            else:
                a, b = _num(a), _num(b)
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        a, b = _num(a), _num(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise FormulaRuntimeError("除零")
            return a / b
        if op == "%":
            if b == 0:
                raise FormulaRuntimeError("模零")
            return a % b
    if kind == "call":
        name, args = node[1], node[2]
        if name == "if":  # 短路求值
            cond = evaluate(args[0], ctx)
            if not isinstance(cond, bool):
                raise FormulaRuntimeError(f"if 条件期望 boolean，得到 {_typename(cond)}")
            return evaluate(args[1], ctx) if cond else evaluate(args[2], ctx) if len(args) > 2 else None
        if name == "prop":
            key = args[0][1]
            if key not in ctx.props:
                raise FormulaRuntimeError(f"属性 {key!r} 不存在")
            return ctx.props[key]
        if name == "prop_cf":
            key = args[0][1]
            return ctx.custom.get(key)  # 缺失 = null
        if name == "sub_count":
            return ctx.sub_count
        if name == "sub_done_count":
            return ctx.sub_done_count
        if name == "sub_sum":
            key = args[0][1]
            if not ctx.sub_values:
                return 0
            return sum(_num(v) for row in ctx.sub_values for k, v in row.items() if k == key)
        fn = FUNCTIONS.get(name)
        if fn is None:
            raise FormulaRuntimeError(f"函数 {name!r} 不在白名单")
        return fn(*(evaluate(a, ctx) for a in args))
    raise FormulaRuntimeError(f"未知节点 {kind!r}")


def _loose_eq(a, b) -> bool:
    """宽松相等：number 跨 int/float；date 与 iso text 比较。"""
    if isinstance(a, _dt.date) and isinstance(b, str):
        try:
            b = _dt.date.fromisoformat(b[:10])
        except ValueError:
            return False
    if isinstance(b, _dt.date) and isinstance(a, str):
        try:
            a = _dt.date.fromisoformat(a[:10])
        except ValueError:
            return False
    return a == b


def infer_result_type(node: Node) -> str:
    """静态推断结果类型（保存时；混合分支拒绝——TASK-014 §2.3）。"""
    kind = node[0]
    if kind == "num":
        return "number"
    if kind == "str":
        return "text"
    if kind == "bool":
        return "boolean"
    if kind == "neg":
        return "number"
    if kind == "op2":
        op = node[1]
        if op in ("and", "or", "==", "!=", "<", "<=", ">", ">="):
            return "boolean"
        t = infer_result_type(node[2])
        u = infer_result_type(node[3])
        if op in ("+", "-", "*", "/", "%"):
            types = {t, u}
            if types <= {"number"} or "any" in types:
                # any（prop/prop_cf 静态不窄化）参与算术：结果按 number 推断，
                # 实际类型不符由运行期显式报错（BR-05）
                return "number"
            raise FormulaSyntaxError(f"算术操作数类型不符：{t} {op} {u}")
        return t
    if kind == "call":
        name = node[1]
        returns = {
            "subtract": "number",
            "round": "number",
            "abs": "number",
            "if": None,
            "gt": "boolean",
            "gte": "boolean",
            "lt": "boolean",
            "lte": "boolean",
            "eq": "boolean",
            "ne": "boolean",
            "not": "boolean",
            "days_between": "number",
            "now": "date",
            "date_add": "date",
            "concat": "text",
            "upper": "text",
            "lower": "text",
            "len": "number",
            "minutes": "number",
            "hours": "number",
            "prop": "any",
            "prop_cf": "any",
            "sub_count": "number",
            "sub_done_count": "number",
            "sub_sum": "number",
        }
        if name == "if":
            branches = [infer_result_type(a) for a in node[2][1:]]
            if len({*branches}) > 1:
                raise FormulaSyntaxError(f"if 分支返回混合类型：{branches}（推断失败拒绝保存）")
            return branches[0]
        r = returns.get(name)
        if r == "any":  # prop/prop_cf 运行时定：静态不窄化（算术混用交运行期 BR-05）
            return "any"
        return r
    raise FormulaSyntaxError(f"无法推断类型节点 {kind!r}")
