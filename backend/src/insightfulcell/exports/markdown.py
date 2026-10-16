"""The report as Markdown: readable as plain text, and rendered nicely by any viewer.

Every piece of text from the data (questions, column names, labels) is escaped, so a cell or
column name can neither format the page nor smuggle in HTML. Charts become the table of their
numbers (what the chart shows, exactly), with the chart itself as an inline SVG image for viewers
that show images.
"""

from __future__ import annotations

import base64
import re
from typing import Final

from insightfulcell.exports.document import (
    Block,
    Bullets,
    Document,
    Figure,
    Heading,
    Note,
    Para,
    Table,
    Text,
)

_INLINE: Final = re.compile(r"([\\`*_\[\]|~])")
#: What would start a heading, quote, list or rule if a value began a line with it.
_BLOCK_START: Final = re.compile(r"^(#|>|-|\+|=|\d+[.)])")


def escape(text: str) -> str:
    """Markdown-safe text: HTML characters as entities, Markdown punctuation backslashed,
    one line (a line break in a value would start a new block)."""
    flat = " ".join(text.split())
    html = flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    inline = _INLINE.sub(r"\\\1", html)
    return _BLOCK_START.sub(r"\\\1", inline)


def render_markdown(doc: Document) -> str:
    lines = [f"# {escape(doc.title)}", "", f"_{escape(doc.meta)}_", ""]
    for block in doc.blocks:
        lines.extend(_block(block))
        lines.append("")
    lines.extend(["---", "", f"_{escape(doc.footer)}_", ""])
    return "\n".join(lines)


def _runs(parts: tuple[Text, ...]) -> str:
    out = []
    for part in parts:
        if part.cite:
            out.append(f" \\[{escape(part.text)}\\]")
        elif part.strong:
            out.append(f"**{escape(part.text)}**")
        else:
            out.append(_spaced(part.text, escape(part.text)))
    return "".join(out).strip()


def _spaced(original: str, body: str) -> str:
    """``body`` with the single spaces that separated ``original`` from its neighbours."""
    if not body:
        return " " if original else ""
    lead = " " if original[:1].isspace() else ""
    trail = " " if original[-1:].isspace() else ""
    return f"{lead}{body}{trail}"


def _block(block: Block) -> list[str]:
    match block:
        case Heading(level=level, text=text):
            return [f"{'#' * level} {escape(text)}"]
        case Para(parts=parts, small=small):
            text = _runs(parts)
            return [f"_{text}_" if small and not text.startswith("_") else text]
        case Note(title=title, text=text):
            return [f"> **{escape(title)}** {escape(text)}"]
        case Bullets(items=items):
            return [f"- {_runs(item)}" for item in items]
        case Table():
            return _table(block)
        case Figure():
            return _figure(block)


def _table(table: Table) -> list[str]:
    def row(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(escape(c) or " " for c in cells) + " |"

    align = "| " + " | ".join("---:" if n else ":---" for n in table.numeric) + " |"
    return [row(table.header), align, *(row(r) for r in table.rows)]


def _figure(figure: Figure) -> list[str]:
    image = base64.b64encode(figure.svg.encode("utf-8")).decode("ascii")
    lines = [
        f"**{escape(figure.title)}**",
        "",
        f"![{escape(figure.title)}](data:image/svg+xml;base64,{image})",
        "",
    ]
    if figure.legend:
        lines.extend([f"_{escape(' · '.join(figure.legend))}_", ""])
    if figure.note:
        lines.extend([f"_{escape(figure.note)}_", ""])
    lines.extend(_table(figure.table))
    return lines
