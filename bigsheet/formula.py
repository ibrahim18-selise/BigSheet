"""Translate Excel-style column formulas into DuckDB SQL expressions.

A formula is evaluated once per row. Columns are referenced as [Column Name]
(or by bare name when the name has no spaces). Example:

    =IF([qty] > 10, [price] * 0.9, [price])
    =[price] / SUM([price])          (SUM of one column = whole-column total)
    =LEFT([name], 3) & "-" & YEAR([order date])

Functions not listed in the table below are passed straight to DuckDB, so any
DuckDB scalar function also works (for example regexp_extract).
"""

import re


class FormulaError(Exception):
    pass


def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(\.\d*)?([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)
  | (?P<string>"([^"]|"")*")
  | (?P<column>\[[^\]]+\])
  | (?P<op><>|<=|>=|[-+*/^&=<>%(),;])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
    """,
    re.VERBOSE,
)


def tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise FormulaError(f"Unexpected character '{text[pos]}' at position {pos + 1}")
        kind = m.lastgroup
        value = m.group(kind)
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "op" and value == ";":
            value = ","  # European argument separator
        tokens.append((kind, value))
    tokens.append(("end", ""))
    return tokens


def _agg(sql_name):
    def handler(p, args):
        if len(args) == 1:
            p.uses_window = True
            return f"{sql_name}({args[0]}) OVER ()"
        raise FormulaError(f"{sql_name.upper()} takes one column here")
    return handler


def _sum(p, args):
    if len(args) == 1:
        p.uses_window = True
        return f"sum({args[0]}) OVER ()"
    return "(" + " + ".join(f"coalesce({a}, 0)" for a in args) + ")"


def _minmax(window_name, row_name):
    def handler(p, args):
        if len(args) == 1:
            p.uses_window = True
            return f"{window_name}({args[0]}) OVER ()"
        return f"{row_name}({', '.join(args)})"
    return handler


def _need(n_min, n_max=None):
    def deco(fn):
        def wrapper(p, args):
            hi = n_min if n_max is None else n_max
            if len(args) < n_min or len(args) > hi:
                raise FormulaError("Wrong number of arguments")
            return fn(p, args)
        return wrapper
    return deco


@_need(2, 3)
def _if(p, a):
    other = a[2] if len(a) == 3 else "NULL"
    return f"(CASE WHEN {a[0]} THEN {a[1]} ELSE {other} END)"


@_need(2)
def _iferror(p, a):
    return f"coalesce(TRY({a[0]}), {a[1]})"


@_need(1, 2)
def _round(p, a):
    n = a[1] if len(a) == 2 else "0"
    return f"round({a[0]}, CAST({n} AS INTEGER))"


@_need(1, 2)
def _roundup(p, a):
    n = a[1] if len(a) == 2 else "0"
    return f"(sign({a[0]}) * ceil(abs({a[0]}) * power(10, {n})) / power(10, {n}))"


@_need(1, 2)
def _rounddown(p, a):
    n = a[1] if len(a) == 2 else "0"
    return f"(sign({a[0]}) * floor(abs({a[0]}) * power(10, {n})) / power(10, {n}))"


@_need(2)
def _mod(p, a):
    return f"((({a[0]}) % ({a[1]}) + ({a[1]})) % ({a[1]}))"


@_need(2, 3)
def _mid(p, a):
    if len(a) == 2:
        return f"substring(CAST({a[0]} AS VARCHAR), CAST({a[1]} AS BIGINT))"
    return f"substring(CAST({a[0]} AS VARCHAR), CAST({a[1]} AS BIGINT), CAST({a[2]} AS BIGINT))"


@_need(1, 2)
def _left(p, a):
    n = a[1] if len(a) == 2 else "1"
    return f"left(CAST({a[0]} AS VARCHAR), CAST({n} AS BIGINT))"


@_need(1, 2)
def _right(p, a):
    n = a[1] if len(a) == 2 else "1"
    return f"right(CAST({a[0]} AS VARCHAR), CAST({n} AS BIGINT))"


@_need(2, 3)
def _find(p, a):
    return f"nullif(strpos(CAST({a[1]} AS VARCHAR), CAST({a[0]} AS VARCHAR)), 0)"


@_need(2, 3)
def _search(p, a):
    return f"nullif(strpos(lower(CAST({a[1]} AS VARCHAR)), lower(CAST({a[0]} AS VARCHAR))), 0)"


@_need(3)
def _datedif(p, a):
    unit = a[2].strip("'").lower()
    units = {"d": "day", "m": "month", "y": "year"}
    if unit not in units:
        raise FormulaError('DATEDIF unit must be "d", "m" or "y"')
    return f"date_diff('{units[unit]}', CAST({a[0]} AS DATE), CAST({a[1]} AS DATE))"


@_need(2)
def _eomonth(p, a):
    return f"last_day(CAST({a[0]} AS DATE) + to_months(CAST({a[1]} AS INTEGER)))"


def _simple(template, n_min, n_max=None):
    @_need(n_min, n_max)
    def handler(p, a):
        return template.format(*a)
    return handler


def _and(p, a):
    return "(" + " AND ".join(a) + ")"


def _or(p, a):
    return "(" + " OR ".join(a) + ")"


def _concat(p, a):
    return "concat(" + ", ".join(a) + ")"


FUNCTIONS = {
    "IF": _if,
    "IFERROR": _iferror,
    "AND": _and,
    "OR": _or,
    "NOT": _simple("(NOT {0})", 1),
    "ISBLANK": _simple("({0} IS NULL)", 1),
    "ISNUMBER": _simple("(TRY_CAST({0} AS DOUBLE) IS NOT NULL)", 1),
    "ROUND": _round,
    "ROUNDUP": _roundup,
    "ROUNDDOWN": _rounddown,
    "INT": _simple("floor({0})", 1),
    "ABS": _simple("abs({0})", 1),
    "SQRT": _simple("sqrt({0})", 1),
    "POWER": _simple("power({0}, {1})", 2),
    "MOD": _mod,
    "LN": _simple("ln({0})", 1),
    "LOG10": _simple("log10({0})", 1),
    "EXP": _simple("exp({0})", 1),
    "CEILING": _simple("ceil({0})", 1, 2),
    "FLOOR": _simple("floor({0})", 1, 2),
    "LEN": _simple("length(CAST({0} AS VARCHAR))", 1),
    "LEFT": _left,
    "RIGHT": _right,
    "MID": _mid,
    "UPPER": _simple("upper(CAST({0} AS VARCHAR))", 1),
    "LOWER": _simple("lower(CAST({0} AS VARCHAR))", 1),
    "TRIM": _simple("trim(CAST({0} AS VARCHAR))", 1),
    "CONCAT": _concat,
    "CONCATENATE": _concat,
    "SUBSTITUTE": _simple("replace(CAST({0} AS VARCHAR), CAST({1} AS VARCHAR), CAST({2} AS VARCHAR))", 3),
    "FIND": _find,
    "SEARCH": _search,
    "VALUE": _simple("TRY_CAST({0} AS DOUBLE)", 1),
    "EXACT": _simple("(CAST({0} AS VARCHAR) = CAST({1} AS VARCHAR))", 2),
    "TODAY": _simple("current_date", 0),
    "NOW": _simple("now()", 0),
    "YEAR": _simple("year({0})", 1),
    "MONTH": _simple("month({0})", 1),
    "DAY": _simple("day({0})", 1),
    "HOUR": _simple("hour({0})", 1),
    "MINUTE": _simple("minute({0})", 1),
    "SECOND": _simple("second({0})", 1),
    "WEEKDAY": _simple("(dayofweek({0}) + 1)", 1),
    "DATE": _simple("make_date(CAST({0} AS BIGINT), CAST({1} AS BIGINT), CAST({2} AS BIGINT))", 3),
    "DATEDIF": _datedif,
    "EOMONTH": _eomonth,
    "SUM": _sum,
    "AVERAGE": _agg("avg"),
    "MEDIAN": _agg("median"),
    "COUNT": _agg("count"),
    "COUNTA": _agg("count"),
    "MIN": _minmax("min", "least"),
    "MAX": _minmax("max", "greatest"),
}


class _Parser:
    def __init__(self, text, columns):
        self.tokens = tokenize(text)
        self.i = 0
        self.columns = {c.lower(): c for c in columns}
        self.uses_window = False
        self.referenced = []

    def peek(self):
        return self.tokens[self.i]

    def take(self, value=None):
        tok = self.tokens[self.i]
        if value is not None and tok[1] != value:
            got = tok[1] or "end of formula"
            raise FormulaError(f"Expected '{value}' but found '{got}'")
        self.i += 1
        return tok

    def column(self, name):
        real = self.columns.get(name.lower())
        if real is None:
            raise FormulaError(f"Unknown column [{name}]")
        if real not in self.referenced:
            self.referenced.append(real)
        return quote_ident(real)

    def parse(self):
        sql = self.comparison()
        if self.peek()[0] != "end":
            raise FormulaError(f"Unexpected '{self.peek()[1]}'")
        return sql

    def comparison(self):
        left = self.concat()
        while self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take()[1]
            right = self.concat()
            left = f"({left} {op} {right})"
        return left

    def concat(self):
        left = self.additive()
        while self.peek()[1] == "&":
            self.take()
            right = self.additive()
            left = f"(coalesce(CAST({left} AS VARCHAR), '') || coalesce(CAST({right} AS VARCHAR), ''))"
        return left

    def additive(self):
        left = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            right = self.term()
            left = f"({left} {op} {right})"
        return left

    def term(self):
        left = self.power()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            right = self.power()
            left = f"({left} {op} {right})"
        return left

    def power(self):
        left = self.unary()
        while self.peek()[1] == "^":
            self.take()
            right = self.unary()
            left = f"power({left}, {right})"
        return left

    def unary(self):
        if self.peek()[1] in ("-", "+"):
            op = self.take()[1]
            return f"({op}{self.unary()})"
        value = self.primary()
        if self.peek()[1] == "%":
            self.take()
            value = f"({value} / 100.0)"
        return value

    def primary(self):
        kind, value = self.peek()
        if kind == "number":
            self.take()
            return value
        if kind == "string":
            self.take()
            return sql_string(value[1:-1].replace('""', '"'))
        if kind == "column":
            self.take()
            return self.column(value[1:-1].strip())
        if value == "(":
            self.take()
            inner = self.comparison()
            self.take(")")
            return f"({inner})"
        if kind == "ident":
            self.take()
            if self.peek()[1] == "(":
                return self.call(value)
            upper = value.upper()
            if upper in ("TRUE", "FALSE"):
                return upper.lower()
            return self.column(value)
        raise FormulaError(f"Unexpected '{value or 'end of formula'}'")

    def call(self, name):
        self.take("(")
        args = []
        if self.peek()[1] != ")":
            args.append(self.comparison())
            while self.peek()[1] == ",":
                self.take()
                args.append(self.comparison())
        self.take(")")
        handler = FUNCTIONS.get(name.upper())
        if handler is None:
            # Pass unknown functions through to DuckDB.
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise FormulaError(f"Unknown function {name}")
            return f"{name.lower()}({', '.join(args)})"
        try:
            return handler(self, args)
        except FormulaError as e:
            raise FormulaError(f"{name.upper()}: {e}")


def translate(formula, columns):
    """Return (sql_expression, uses_window, referenced_columns)."""
    text = formula.strip()
    if text.startswith("="):
        text = text[1:]
    if not text:
        raise FormulaError("The formula is empty")
    parser = _Parser(text, columns)
    sql = parser.parse()
    return sql, parser.uses_window, parser.referenced
