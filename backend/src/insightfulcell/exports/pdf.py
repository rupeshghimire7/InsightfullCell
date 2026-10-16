"""The report as a PDF, built with fpdf2 (pure Python: no browser, no system libraries).

A4, the bundled DejaVu Sans (so every character a dataset uses prints, not only Latin-1),
Word-like structure: headings, paragraphs with source tags, notes, tables, and charts and maps
as pictures of the same SVG the HTML export shows, each followed by its numbers. Text from the
data is laid out as text, never interpreted (no markup mode).
"""

from __future__ import annotations

import io
import logging
from typing import Final

from fpdf import FPDF
from fpdf.fonts import FontFace
from PIL import Image

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
from insightfulcell.exports.images import font_path, svg_to_png

FONT: Final = "DejaVu"
INK: Final = (31, 36, 33)
MUTED: Final = (94, 98, 94)
ACCENT: Final = (47, 111, 98)
PANEL: Final = (244, 243, 241)
LINE: Final = (222, 220, 214)
_HEADING_SIZE: Final = {1: 18.0, 2: 14.0, 3: 11.5}

# fpdf2 logs every glyph a font lacks (emoji in a value): one warning per character is noise.
logging.getLogger("fpdf.output").setLevel(logging.ERROR)


class _Pdf(FPDF):
    footer_text = ""

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(FONT, size=7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"{self.footer_text}   ·   page {self.page_no()}/{{nb}}", align="C")


def render_pdf(doc: Document) -> bytes:
    pdf = _Pdf(orientation="portrait", unit="mm", format="A4")
    pdf.footer_text = f"InsightfulCell report · {doc.dataset_name or 'untitled'}"
    pdf.set_title(doc.title)
    pdf.set_subject(doc.dataset_name)
    pdf.set_author("InsightfulCell")
    pdf.set_creator("InsightfulCell")
    pdf.add_font(FONT, "", str(font_path()))
    pdf.add_font(FONT, "B", str(font_path(bold=True)))
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    _heading(pdf, 1, doc.title)
    _paragraph(pdf, (Text(doc.meta),), small=True)
    for block in doc.blocks:
        _block(pdf, block)
    pdf.ln(4)
    _paragraph(pdf, (Text(doc.footer),), small=True)
    return bytes(pdf.output())


def _block(pdf: _Pdf, block: Block) -> None:
    match block:
        case Heading(level=level, text=text):
            _heading(pdf, level, text)
        case Para(parts=parts, small=small):
            _paragraph(pdf, parts, small=small)
        case Note(title=title, text=text):
            _note(pdf, title, text)
        case Bullets(items=items, small=small):
            _bullets(pdf, items, small=small)
        case Table():
            _table(pdf, block)
        case Figure():
            _figure(pdf, block)


def _heading(pdf: _Pdf, level: int, text: str) -> None:
    size = _HEADING_SIZE.get(level, 11.0)
    if level <= 2:
        pdf.ln(3)
    if pdf.will_page_break(size * 1.6):  # never a heading alone at the foot of a page
        pdf.add_page()
    pdf.set_font(FONT, "B", size)
    pdf.set_text_color(*(ACCENT if level == 2 else INK))
    pdf.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")
    if level == 2:
        pdf.set_draw_color(*LINE)
        pdf.line(pdf.l_margin, pdf.get_y() + 0.5, pdf.w - pdf.r_margin, pdf.get_y() + 0.5)
    pdf.ln(2)


def _write(pdf: _Pdf, parts: tuple[Text, ...], *, small: bool) -> None:
    size = 8.5 if small else 10.0
    height = size * 0.48
    for part in parts:
        if part.cite:
            pdf.set_font(FONT, "B", size - 2)
            pdf.set_text_color(*ACCENT)
            pdf.write(height, f" {part.text} ")
            continue
        pdf.set_font(FONT, "B" if part.strong else "", size)
        pdf.set_text_color(*(MUTED if small else INK))
        pdf.write(height, part.text)
    pdf.ln(height)


def _paragraph(pdf: _Pdf, parts: tuple[Text, ...], *, small: bool = False) -> None:
    _write(pdf, parts, small=small)
    pdf.ln(1.5)


def _bullets(pdf: _Pdf, items: tuple[tuple[Text, ...], ...], *, small: bool) -> None:
    left = pdf.l_margin
    for item in items:
        pdf.set_x(left)
        pdf.set_font(FONT, "", 8.5 if small else 10.0)
        pdf.set_text_color(*MUTED)
        pdf.write(4.5, "•")
        pdf.set_left_margin(left + 4)
        pdf.set_x(left + 4)
        _write(pdf, item, small=small)
        pdf.set_left_margin(left)
        pdf.ln(0.8)
    pdf.ln(1)


def _note(pdf: _Pdf, title: str, text: str) -> None:
    pdf.set_fill_color(*PANEL)
    pdf.set_text_color(*INK)
    pdf.set_font(FONT, "B", 10)
    pdf.multi_cell(0, 6, title, fill=True, new_x="LMARGIN", new_y="NEXT", padding=(2, 3, 0, 3))
    pdf.set_font(FONT, "", 9.5)
    pdf.multi_cell(0, 5, text, fill=True, new_x="LMARGIN", new_y="NEXT", padding=(0, 3, 2, 3))
    pdf.ln(3)


def _table(pdf: _Pdf, table: Table) -> None:
    pdf.set_font(FONT, "", 8.5)
    pdf.set_text_color(*INK)
    pdf.set_draw_color(*LINE)
    widths = _widths(table)
    with pdf.table(
        col_widths=widths,
        text_align=tuple("RIGHT" if n else "LEFT" for n in table.numeric),
        headings_style=FontFace(emphasis="BOLD", fill_color=PANEL),
        line_height=4.6,
        borders_layout="HORIZONTAL_LINES",
        first_row_as_headings=True,
    ) as grid:
        for row in (table.header, *table.rows):
            cells = grid.row()
            for value in row:
                cells.cell(value)
    pdf.ln(3)


def _widths(table: Table) -> tuple[float, ...]:
    """Columns as wide as their longest text (within limits), so labels get the room."""
    longest = [
        max([len(table.header[i]), *(len(r[i]) for r in table.rows)])
        for i in range(len(table.header))
    ]
    return tuple(float(min(max(n, 6), 60)) for n in longest)


def _figure(pdf: _Pdf, figure: Figure) -> None:
    png = svg_to_png(figure.svg)
    with Image.open(io.BytesIO(png)) as picture:
        aspect = picture.height / picture.width
    width = pdf.epw
    height = width * aspect
    if pdf.will_page_break(height + 8):
        pdf.add_page()
    pdf.set_font(FONT, "B", 10)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 5, figure.title, new_x="LMARGIN", new_y="NEXT")
    pdf.image(io.BytesIO(png), w=width, h=height)
    pdf.ln(1)
    if figure.legend:
        _paragraph(pdf, (Text(" · ".join(figure.legend)),), small=True)
    if figure.note:
        _paragraph(pdf, (Text(figure.note),), small=True)
    _table(pdf, figure.table)
