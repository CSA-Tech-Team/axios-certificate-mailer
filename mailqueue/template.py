"""Handlebars-style ({{tag}}) template parsing and rendering.

Supported subset of Handlebars:
  {{name}}          value, HTML-escaped when rendering an HTML template
  {{{name}}}        value, never escaped   ({{&name}} is an alias)
  {{[first name]}}  column names containing spaces
  {{! comment }}    removed ({{!-- comment --}} too)
Anything else (block helpers like {{#if}}/{{#each}}, partials, {{else}},
helper calls like {{upper name}}, sub-expressions) is rejected with a
TemplateSyntaxError rather than being sent half-rendered.

Tags match CSV columns case-insensitively and ignoring surrounding whitespace.
"""
from __future__ import annotations

import html as _html
import re
from typing import Iterable

_TOKEN = re.compile(
    r"\{\{\{(?P<raw3>.*?)\}\}\}"
    r"|\{\{!--(?P<c1>.*?)--\}\}"
    r"|\{\{!(?P<c2>.*?)\}\}"
    r"|\{\{(?P<sigil>[#/>^&]?)(?P<body>.*?)\}\}",
    re.S,
)


class RenderError(Exception):
    pass


class TemplateSyntaxError(ValueError):
    pass


def norm_key(name: str) -> str:
    return " ".join(name.split()).lower()


def _variable(expr: str, whole: str) -> str:
    expr = expr.strip()
    if expr.startswith("[") and expr.endswith("]") and len(expr) > 2:
        return expr[1:-1].strip()
    if not expr or re.search(r"[\s()@|]", expr) or expr == "else" or expr.startswith(("this", ".")):
        raise TemplateSyntaxError(
            f"unsupported Handlebars expression {whole!r}: only {{{{name}}}}, {{{{{{name}}}}}}, "
            "{{[name with spaces]}} and {{! comments }} are supported"
        )
    return expr


def _tokens(text: str):
    """Yield (kind, start, end, name|None, raw: bool) for each tag; kind in 'var'|'comment'."""
    for m in _TOKEN.finditer(text or ""):
        whole = m.group(0)
        if m.group("raw3") is not None:
            yield "var", m.start(), m.end(), _variable(m.group("raw3"), whole), True
        elif m.group("c1") is not None or m.group("c2") is not None:
            yield "comment", m.start(), m.end(), None, False
        else:
            sigil = m.group("sigil")
            if sigil in ("#", "/", ">", "^"):
                raise TemplateSyntaxError(
                    f"unsupported Handlebars construct {whole!r}: blocks, partials and inverse "
                    "sections are not supported; put the logic in the CSV data instead"
                )
            yield "var", m.start(), m.end(), _variable(m.group("body"), whole), sigil == "&"


def find_tags(text: str) -> list[tuple[str, str]]:
    """Ordered unique (normalized, as-written) variable names. Raises TemplateSyntaxError."""
    seen: dict[str, str] = {}
    for kind, _s, _e, name, _raw in _tokens(text):
        if kind == "var":
            seen.setdefault(norm_key(name), name)
    return list(seen.items())


def tags_in(texts: Iterable[str | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in texts:
        for k, raw in find_tags(t or ""):
            out.setdefault(k, raw)
    return out


def missing_tags(texts: Iterable[str | None], columns: Iterable[str]) -> list[str]:
    cols = {norm_key(c) for c in columns}
    return [raw for k, raw in tags_in(texts).items() if k not in cols]


def render(text: str, values: dict[str, str], html: bool = False) -> str:
    """values is keyed by norm_key(column). Missing/empty values raise RenderError.

    With html=True, {{x}} is HTML-escaped and {{{x}}} is not. With html=False
    (subject, plain text) nothing is escaped.
    """
    out: list[str] = []
    pos = 0
    for kind, start, end, name, raw in _tokens(text):
        out.append(text[pos:start])
        pos = end
        if kind == "comment":
            continue
        key = norm_key(name)
        if key not in values:
            raise RenderError(f"no value for tag {{{{{name}}}}}")
        val = values[key]
        if not val.strip():
            raise RenderError(f"empty value for tag {{{{{name}}}}}")
        out.append(val if (raw or not html) else _html.escape(val))
    out.append(text[pos:])
    return "".join(out)
