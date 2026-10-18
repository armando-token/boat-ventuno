"""Pure-Python deterministic PDF 1.4 generator with visual tables, slide layouts, and zero raw Markdown syntax (B7, B11, DOC-01, H15, R8-F06)."""

from __future__ import annotations

from pathlib import Path
import re
import textwrap
from typing import Any, Dict, List, Optional, Tuple

# Legibility floor, expressed as rendered glyph-box height (what `pdftotext -bbox` reports):
# body text >= 8.0 pt, tables and code >= 7.5 pt. The box is 0.925 em for Helvetica and
# 0.786 em for Courier (Adobe AFM ascender - descender), so the nominal font sizes are larger.
# Documents that do not fit their page limit at these sizes must be condensed: the
# generator raises PDF_PAGE_OVERFLOW / PDF_FONT_FLOOR instead of shrinking the text.
HELVETICA_GLYPH_EM = 0.925
COURIER_GLYPH_EM = 0.786
MIN_BODY_GLYPH_PT = 8.0
MIN_TABLE_GLYPH_PT = 7.5
MIN_CODE_GLYPH_PT = 7.5
MIN_BODY_FONT_PT = round(MIN_BODY_GLYPH_PT / HELVETICA_GLYPH_EM + 0.05, 1)  # 8.7
MIN_TABLE_FONT_PT = round(MIN_TABLE_GLYPH_PT / HELVETICA_GLYPH_EM + 0.05, 1)  # 8.2
MIN_CODE_FONT_PT = round(MIN_CODE_GLYPH_PT / COURIER_GLYPH_EM + 0.05, 1)  # 9.6


def _check_font_floor(body_font_size: float, table_font_size: float) -> None:
    if body_font_size < MIN_BODY_FONT_PT or table_font_size < MIN_TABLE_FONT_PT:
        raise ValueError(
            f"PDF_FONT_FLOOR: body {body_font_size} pt / table {table_font_size} pt is below the legibility "
            f"floor ({MIN_BODY_FONT_PT} / {MIN_TABLE_FONT_PT} pt); condense the Markdown instead of shrinking the font"
        )


def _escape_pdf_text(s: str) -> str:
    """Convert common unicode symbols and any residual LaTeX tokens to clean ASCII equivalents for Type1 Helvetica/Courier."""
    latex_replacements = {
        "\\times": "x",
        "\\rightarrow": "->",
        "\\leftrightarrow": "<->",
        "\\le": "<=",
        "\\ge": ">=",
        "\\pm": "+/-",
        "\\ll": "<<",
        "\\binom": "C",
        "\\text{": "",
        "$": "",
    }
    for k, v in latex_replacements.items():
        s = s.replace(k, v)
    replacements = {
        "—": "--",
        "–": "-",
        "→": "->",
        "↔": "<->",
        "≤": "<=",
        "≥": ">=",
        "≈": "~=",
        "×": "x",
        "°": " deg ",
        "±": "+/-",
        "≪": "<<",
        "²": "^2",
        "³": "^3",
        "₂": "2",
        "“": '"',
        "”": '"',
        "’": "'",
        "‘": "'",
        "′": "'",
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        "¿": "?",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s_ascii = s.encode("ascii", errors="replace").decode("ascii")
    return s_ascii.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def strip_markdown_inline(text: str) -> str:
    """Remove raw Markdown inline formatting tokens (**, `, *, leading #, [label](url)) while preserving readable text."""
    s = text.strip()
    s = re.sub(r"^#{1,6}\s+", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = s.replace("**", "")
    s = s.replace("`", "")
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    return s


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return all(bool(re.match(r"^:?-{2,}:?$", c)) for c in cells if c)


def _parse_markdown_blocks(md_text: str) -> List[Dict[str, Any]]:
    """Parse Markdown text into structured blocks: h1, h2, meta, paragraph, bullet, code, table."""
    lines = md_text.splitlines()
    blocks: List[Dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if s == "<!-- pdf-ignore-start -->":
            i += 1
            while i < n and lines[i].strip() != "<!-- pdf-ignore-end -->":
                i += 1
            if i < n:
                i += 1
            continue
        if not s or s == "---":
            i += 1
            continue
        if s.startswith("```"):
            code_lines: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i].rstrip())
                i += 1
            if i < n:
                i += 1
            blocks.append({"type": "code", "lines": code_lines})
            continue
        if s.startswith("|") and s.endswith("|"):
            table_rows: List[List[str]] = []
            while i < n and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                row_str = lines[i].strip()
                if not _is_table_separator(row_str):
                    cells = [strip_markdown_inline(c) for c in row_str.strip("|").split("|")]
                    table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append({"type": "table", "rows": table_rows})
            continue
        if s.startswith("# "):
            blocks.append({"type": "h1", "text": strip_markdown_inline(s[2:])})
            i += 1
            continue
        if s.startswith("## ") or s.startswith("### "):
            blocks.append({"type": "h2", "text": strip_markdown_inline(s)})
            i += 1
            continue
        if s.startswith("**To:**") or s.startswith("**From:**") or s.startswith("**Subject:**"):
            blocks.append({"type": "meta", "text": strip_markdown_inline(s)})
            i += 1
            continue
        if s.startswith("- ") or re.match(r"^\d+\.\s+", s):
            indent = len(raw) - len(raw.lstrip(" "))
            cleaned = strip_markdown_inline(re.sub(r"^-\s+", "", s))
            blocks.append({"type": "bullet", "text": cleaned, "indent": indent})
            i += 1
            continue
        blocks.append({"type": "paragraph", "text": strip_markdown_inline(s)})
        i += 1
    return blocks


def _build_pdf_from_page_streams(page_streams: List[str], out_path: Path) -> None:
    """Serialize a list of raw PDF content stream strings into a valid PDF-1.4 document."""
    objects: List[bytes] = []

    def add_obj(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"")
    font_regular_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font_mono_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    page_obj_ids: List[int] = []
    for stream_str in page_streams:
        stream_bytes = stream_str.encode("ascii")
        content_id = add_obj(
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii") + stream_bytes + b"\nendstream"
        )
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R /F3 {font_mono_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_id = add_obj(page_dict)
        page_obj_ids.append(page_id)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii")

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for idx, obj_bytes in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf.extend(f"{idx} 0 obj\n".encode("ascii"))
        buf.extend(obj_bytes)
        buf.extend(b"\nendobj\n")

    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    buf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    out_path.write_bytes(bytes(buf))


def write_multi_page_pdf(
    pages_lines: List[List[str]],
    out_path: Path,
    font_size: int = 9,
    leading: int = 11,
    start_y: int = 756,
    min_bottom_y: int = 24,
) -> None:
    """Write a valid PDF-1.4 file with exact page count `len(pages_lines)` and fail-closed overflow protection."""
    _check_font_floor(font_size, font_size)
    max_lines_per_page = max(1, (start_y - min_bottom_y) // max(1, leading))
    for page_idx, lines in enumerate(pages_lines, start=1):
        if len(lines) > max_lines_per_page:
            raise ValueError(
                f"PDF_PAGE_OVERFLOW in {out_path.name} page {page_idx}: "
                f"{len(lines)} lines > max {max_lines_per_page} lines at font_size={font_size}, leading={leading}. "
                f"Silent line truncation is forbidden (H15)."
            )

    page_streams: List[str] = []
    for lines in pages_lines:
        stream_cmds: List[str] = ["BT", f"/F1 {font_size} Tf", f"36 {start_y} Td", f"{leading} TL"]
        for line in lines:
            clean = strip_markdown_inline(line)
            is_heading = line.strip().startswith("#") or clean.startswith("To:") or clean.startswith("Slide ")
            esc = _escape_pdf_text(clean)
            if is_heading:
                stream_cmds.append(f"/F2 {font_size + 1} Tf ({esc}) Tj /F1 {font_size} Tf T*")
            else:
                stream_cmds.append(f"({esc}) Tj T*")
        stream_cmds.append("ET")
        page_streams.append("\n".join(stream_cmds))

    _build_pdf_from_page_streams(page_streams, out_path)


class _PageCanvas:
    """Vector + text PDF page builder supporting tables, banners, and multi-page layout with overflow protection."""

    def __init__(
        self,
        *,
        max_pages: int,
        margin_x: float = 28.0,
        top_y: float = 764.0,
        bottom_y: float = 26.0,
        body_font_size: float = MIN_BODY_FONT_PT,
        body_leading: Optional[float] = None,
        table_font_size: float = MIN_TABLE_FONT_PT,
        table_leading: Optional[float] = None,
    ) -> None:
        _check_font_floor(body_font_size, table_font_size)
        self.max_pages = max_pages
        self.margin_x = margin_x
        self.content_w = 612.0 - 2.0 * margin_x
        self.top_y = top_y
        self.bottom_y = bottom_y
        self.body_font_size = body_font_size
        self.body_leading = body_leading if body_leading is not None else round(body_font_size * 1.18, 2)
        self.table_font_size = table_font_size
        self.table_leading = table_leading if table_leading is not None else round(table_font_size * 1.13, 2)
        self.pages: List[List[str]] = [[]]
        self.y = top_y

    def _new_page(self) -> None:
        if len(self.pages) >= self.max_pages:
            raise ValueError(
                f"PDF_PAGE_OVERFLOW: Document exceeded max_pages={self.max_pages} at y={self.y:.1f}"
            )
        self.pages.append([])
        self.y = self.top_y

    def _ensure_space(self, height: float) -> None:
        if self.y - height < self.bottom_y:
            self._new_page()

    def add_cmd(self, cmd: str) -> None:
        self.pages[-1].append(cmd)

    def draw_text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        font: str = "F1",
        size: float = 8.0,
        rgb: Tuple[float, float, float] = (0.1, 0.14, 0.2),
    ) -> None:
        r, g, b = rgb
        esc = _escape_pdf_text(text)
        self.add_cmd(f"BT /{font} {size:.2f} Tf {r:.2f} {g:.2f} {b:.2f} rg {x:.2f} {y:.2f} Td ({esc}) Tj ET")

    def draw_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill_rgb: Optional[Tuple[float, float, float]] = None,
        stroke_rgb: Optional[Tuple[float, float, float]] = None,
        line_w: float = 0.5,
    ) -> None:
        cmds: List[str] = ["q"]
        if fill_rgb is not None:
            cmds.append(f"{fill_rgb[0]:.2f} {fill_rgb[1]:.2f} {fill_rgb[2]:.2f} rg")
        if stroke_rgb is not None:
            cmds.append(f"{stroke_rgb[0]:.2f} {stroke_rgb[1]:.2f} {stroke_rgb[2]:.2f} RG {line_w:.2f} w")
        op = "B" if (fill_rgb and stroke_rgb) else ("f" if fill_rgb else "S")
        cmds.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {op} Q")
        self.add_cmd(" ".join(cmds))

    def render_h1(self, title: str) -> None:
        banner_h = self.body_leading * 1.95
        self._ensure_space(banner_h + 4.0)
        self.draw_rect(
            self.margin_x,
            self.y - banner_h,
            self.content_w,
            banner_h,
            fill_rgb=(0.10, 0.21, 0.36),
        )
        self.draw_text(
            self.margin_x + 8.0,
            self.y - banner_h + (banner_h - (self.body_font_size + 2.0)) * 0.5 + 1.5,
            title,
            font="F2",
            size=self.body_font_size + 2.0,
            rgb=(1.0, 1.0, 1.0),
        )
        self.y -= banner_h + 4.0

    def render_h2(self, heading: str) -> None:
        h = self.body_leading * 1.35
        self._ensure_space(h + 3.0)
        self.draw_text(
            self.margin_x,
            self.y - self.body_leading,
            heading,
            font="F2",
            size=self.body_font_size + 0.8,
            rgb=(0.11, 0.24, 0.44),
        )
        self.draw_rect(
            self.margin_x,
            self.y - self.body_leading - 2.0,
            self.content_w,
            0.6,
            fill_rgb=(0.75, 0.82, 0.90),
        )
        self.y -= h + 2.0

    def render_paragraph(self, text: str, *, bullet: bool = False, indent: int = 0, bold_prefix: bool = False) -> None:
        x_offset = (10.0 if bullet else 0.0) + (8.0 if indent >= 2 else 0.0)
        avail_w = self.content_w - x_offset
        chars_per_line = max(30, int(avail_w / (self.body_font_size * 0.50)))
        prefix = "* " if bullet else ""
        wrapped = textwrap.wrap(prefix + text, width=chars_per_line, break_long_words=False, break_on_hyphens=False)
        if not wrapped:
            wrapped = [""]
        needed_h = len(wrapped) * self.body_leading + 1.5
        self._ensure_space(needed_h)
        for idx, ln in enumerate(wrapped):
            self.draw_text(
                self.margin_x + x_offset,
                self.y - self.body_leading,
                ln,
                font="F2" if (bold_prefix and idx == 0) else "F1",
                size=self.body_font_size,
                rgb=(0.12, 0.15, 0.20),
            )
            self.y -= self.body_leading
        self.y -= 1.5

    def render_code_block(self, lines: List[str]) -> None:
        code_size = max(MIN_CODE_FONT_PT, self.body_font_size - 1.1)
        code_leading = round(code_size * 1.06, 2)
        chars_per_line = max(50, int((self.content_w - 12.0) / (code_size * 0.60)))
        wrapped_all: List[str] = []
        for raw_ln in lines:
            clean_ln = raw_ln.rstrip()
            if not clean_ln:
                wrapped_all.append("")
            else:
                parts = textwrap.wrap(
                    clean_ln,
                    width=chars_per_line,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                wrapped_all.extend(parts if parts else [""])
        idx = 0
        while idx < len(wrapped_all):
            avail_h = self.y - self.bottom_y - 4.0
            max_lines_here = int((avail_h - 6.0) // code_leading)
            if max_lines_here < 3:
                self._new_page()
                avail_h = self.y - self.bottom_y - 4.0
                max_lines_here = int((avail_h - 6.0) // code_leading)
            chunk = wrapped_all[idx : idx + max_lines_here]
            box_h = len(chunk) * code_leading + 5.0
            self.draw_rect(
                self.margin_x,
                self.y - box_h,
                self.content_w,
                box_h,
                fill_rgb=(0.95, 0.96, 0.98),
                stroke_rgb=(0.78, 0.82, 0.88),
                line_w=0.4,
            )
            cur_y = self.y - code_leading - 1.0
            for cln in chunk:
                if cln.strip():
                    self.draw_text(
                        self.margin_x + 6.0,
                        cur_y,
                        cln,
                        font="F3",
                        size=code_size,
                        rgb=(0.14, 0.18, 0.26),
                    )
                cur_y -= code_leading
            self.y -= box_h + 3.0
            idx += len(chunk)

    def _fit_column_widths(self, rows: List[List[str]], weights: List[float]) -> List[float]:
        """Share the width by `weights`, but give every column room for its longest unbreakable token when possible."""
        char_w = self.table_font_size * 0.57
        need = [
            min(
                max((len(tok) for r in rows for tok in r[c].split()), default=1) * char_w + 7.5,
                0.30 * self.content_w,
            )
            for c in range(len(weights))
        ]
        if sum(need) > self.content_w:
            total = sum(weights)
            return [self.content_w * (w / total) for w in weights]
        fixed: Dict[int, float] = {}
        while True:
            free_w = self.content_w - sum(fixed.values())
            free_weight = sum(w for c, w in enumerate(weights) if c not in fixed)
            widths = [fixed.get(c, free_w * w / free_weight) for c, w in enumerate(weights)]
            short = [c for c in range(len(weights)) if c not in fixed and widths[c] < need[c]]
            if not short:
                return widths
            for c in short:
                fixed[c] = need[c]

    def render_table(self, rows: List[List[str]], col_weights: Optional[List[float]] = None) -> None:
        if not rows:
            return
        num_cols = max(len(r) for r in rows)
        norm_rows = [r + [""] * (num_cols - len(r)) for r in rows]
        if col_weights is None or len(col_weights) != num_cols:
            # Estimate weights from 75th-percentile / max cell length per column, clamped so narrow numeric columns stay compact
            raw_weights: List[float] = []
            for c in range(num_cols):
                max_len = max(len(r[c]) for r in norm_rows)
                raw_weights.append(max(8.0, min(62.0, float(max_len) ** 0.72)))
            col_widths = self._fit_column_widths(norm_rows, raw_weights)
        else:
            total_w = sum(col_weights)
            col_widths = [self.content_w * (w / total_w) for w in col_weights]

        for r_idx, row in enumerate(norm_rows):
            is_header = r_idx == 0
            wrapped_cells: List[List[str]] = []
            for c_idx, cell_text in enumerate(row):
                cw = col_widths[c_idx] - 7.0
                char_cap = max(8, int(cw / (self.table_font_size * 0.57)))
                # Identifiers longer than the column are split (after "::" first) rather than drawn across the border.
                cell_text = " ".join(
                    tok.replace("::", ":: ") if len(tok) > char_cap else tok for tok in cell_text.split()
                )
                w_lines = textwrap.wrap(
                    cell_text,
                    width=char_cap,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
                wrapped_cells.append(w_lines if w_lines else [""])
            max_cell_lines = max(len(cl) for cl in wrapped_cells)
            row_h = max_cell_lines * self.table_leading + 4.0
            self._ensure_space(row_h)

            cur_x = self.margin_x
            fill_color = (
                (0.13, 0.25, 0.42)
                if is_header
                else ((0.95, 0.97, 1.00) if r_idx % 2 == 1 else (1.0, 1.0, 1.0))
            )
            text_color = (1.0, 1.0, 1.0) if is_header else (0.10, 0.14, 0.20)
            for c_idx, clines in enumerate(wrapped_cells):
                cw = col_widths[c_idx]
                self.draw_rect(
                    cur_x,
                    self.y - row_h,
                    cw,
                    row_h,
                    fill_rgb=fill_color,
                    stroke_rgb=(0.72, 0.78, 0.86),
                    line_w=0.4,
                )
                line_y = self.y - self.table_leading - 1.0
                for ln in clines:
                    if ln:
                        self.draw_text(
                            cur_x + 3.0,
                            line_y,
                            ln,
                            font="F2" if is_header else "F1",
                            size=self.table_font_size,
                            rgb=text_color,
                        )
                    line_y -= self.table_leading
                cur_x += cw
            self.y -= row_h
        self.y -= 3.5


def render_markdown_document_to_pdf(
    md_path: Path,
    out_path: Path,
    *,
    max_pages: int,
    body_font_size: float = MIN_BODY_FONT_PT,
    body_leading: Optional[float] = None,
    table_font_size: float = MIN_TABLE_FONT_PT,
    table_leading: Optional[float] = None,
) -> None:
    """Render a structured Markdown document with tables, headings, and code blocks to a visual PDF."""
    blocks = _parse_markdown_blocks(md_path.read_text(encoding="utf-8"))
    canvas = _PageCanvas(
        max_pages=max_pages,
        margin_x=26.0,
        top_y=768.0,
        bottom_y=22.0,
        body_font_size=body_font_size,
        body_leading=body_leading,
        table_font_size=table_font_size,
        table_leading=table_leading,
    )
    for blk in blocks:
        btype = blk["type"]
        if btype == "h1":
            canvas.render_h1(blk["text"])
        elif btype == "h2":
            canvas.render_h2(blk["text"])
        elif btype == "meta":
            canvas.render_paragraph(blk["text"], bold_prefix=True)
        elif btype == "bullet":
            canvas.render_paragraph(blk["text"], bullet=True, indent=blk.get("indent", 0))
        elif btype == "paragraph":
            canvas.render_paragraph(blk["text"], bullet=False)
        elif btype == "code":
            canvas.render_code_block(blk["lines"])
        elif btype == "table":
            canvas.render_table(blk["rows"])

    page_streams = ["\n".join(cmds) for cmds in canvas.pages]
    _build_pdf_from_page_streams(page_streams, out_path)


def render_pitch_slides_to_pdf(md_path: Path, out_path: Path) -> None:
    """Render PITCH_SLIDES.md as a visual 8-slide deck (1 slide per page, <= 10 slides)."""
    pitch_md = md_path.read_text(encoding="utf-8")
    raw_slides = [s.strip() for s in pitch_md.split("---") if s.strip()]
    if len(raw_slides) > 10:
        raise ValueError(f"PITCH_SLIDES.md has {len(raw_slides)} slides > 10 maximum allowed")

    deck_title = "SDV Resource Governor -- Technical Pitch Deck"
    total_slides = len(raw_slides)
    page_streams: List[str] = []

    for idx, sl_md in enumerate(raw_slides, start=1):
        blocks = _parse_markdown_blocks(sl_md)
        slide_heading = f"Slide {idx}"
        content_blocks: List[Dict[str, Any]] = []
        for b in blocks:
            if b["type"] == "h1":
                deck_title = b["text"]
            elif b["type"] == "h2":
                slide_heading = b["text"]
            else:
                content_blocks.append(b)

        canvas = _PageCanvas(
            max_pages=1,
            margin_x=36.0,
            top_y=680.0,
            bottom_y=48.0,
            body_font_size=10.2,
            body_leading=14.5,
            table_font_size=8.5,
            table_leading=11.0,
        )
        # Outer slide frame
        canvas.draw_rect(
            24.0,
            32.0,
            564.0,
            728.0,
            fill_rgb=(0.99, 0.99, 1.00),
            stroke_rgb=(0.76, 0.82, 0.89),
            line_w=0.8,
        )
        # Top slide header banner
        canvas.draw_rect(
            24.0,
            698.0,
            564.0,
            62.0,
            fill_rgb=(0.10, 0.21, 0.36),
        )
        canvas.draw_text(
            38.0,
            738.0,
            deck_title,
            font="F1",
            size=8.5,
            rgb=(0.76, 0.85, 0.96),
        )
        canvas.draw_text(
            38.0,
            714.0,
            slide_heading,
            font="F2",
            size=13.0,
            rgb=(1.0, 1.0, 1.0),
        )
        canvas.draw_text(
            520.0,
            738.0,
            f"Slide {idx} / {total_slides}",
            font="F2",
            size=8.5,
            rgb=(0.90, 0.95, 1.00),
        )
        for cb in content_blocks:
            if cb["type"] == "bullet":
                canvas.render_paragraph(cb["text"], bullet=True, indent=cb.get("indent", 0))
                canvas.y -= 3.0
            elif cb["type"] == "paragraph":
                canvas.render_paragraph(cb["text"], bullet=False)
                canvas.y -= 3.0
            elif cb["type"] == "table":
                canvas.render_table(cb["rows"])

        # Footer bar
        canvas.draw_rect(24.0, 32.0, 564.0, 18.0, fill_rgb=(0.93, 0.95, 0.98))
        canvas.draw_text(
            38.0,
            38.0,
            "sdv-resource-governor | Deterministic 8-Class Physical Resource Governor (MacBook Pro arm64 + Ventuno Q aarch64)",
            font="F1",
            size=7.2,
            rgb=(0.30, 0.36, 0.45),
        )
        page_streams.append("\n".join(canvas.pages[0]))

    _build_pdf_from_page_streams(page_streams, out_path)


def verify_pdf_contains_markdown_lines(md_path: Path, pdf_path: Path, width: int = 100) -> bool:
    """Verify that `pdf_path` has zero raw Markdown tokens and contains all semantic lines/cells of `md_path` (H15, R8-F06)."""
    if not pdf_path.exists():
        return False
    pdf_ascii = pdf_path.read_bytes().decode("ascii", errors="ignore")
    # Extract all rendered text literals (...) Tj along with their font
    font_chunks = re.findall(r"/(F[123])\s+[0-9.]+\s+Tf[^()]*\(((?:\\.|[^\\()])*)\)\s*Tj", pdf_ascii)
    rendered_chunks = re.findall(r"\(((?:\\.|[^\\()])*)\)\s*Tj", pdf_ascii)
    if not rendered_chunks:
        return False
    # Check that no raw Markdown syntax tokens leaked into rendered prose/table text
    for font_id, chunk in font_chunks:
        if font_id in {"F1", "F2"} and (chunk.startswith("# ") or chunk.startswith("## ") or "**" in chunk or "|---|" in chunk):
            return False
    for chunk in rendered_chunks:
        if "**" in chunk or "|---|" in chunk:
            return False

    full_rendered_text = " ".join(rendered_chunks)
    # Normalize escaped parens
    full_rendered_norm = re.sub(r"\s+", " ", full_rendered_text.replace("\\(", "(").replace("\\)", ")")).strip()
    full_rendered_compact = full_rendered_norm.replace(" ", "")

    blocks = _parse_markdown_blocks(md_path.read_text(encoding="utf-8"))
    for blk in blocks:
        btype = blk["type"]
        if btype in {"h1", "h2", "meta", "paragraph", "bullet"}:
            expected = _escape_pdf_text(blk["text"]).replace("\\(", "(").replace("\\)", ")")
            expected_norm = re.sub(r"\s+", " ", expected).strip()
            if expected_norm and expected_norm not in full_rendered_norm:
                return False
        elif btype == "table":
            # Cells may split an over-long identifier across lines, so compare them whitespace-free.
            for row in blk["rows"]:
                for cell in row:
                    expected = _escape_pdf_text(cell).replace("\\(", "(").replace("\\)", ")")
                    expected_compact = re.sub(r"\s+", "", expected)
                    if expected_compact and expected_compact not in full_rendered_compact:
                        return False
    return True


def generate_all_deliverable_pdfs(repo_root: Path) -> None:
    docs_dir = repo_root / "docs"

    # 1. PHYSICAL_ACCOUNT.pdf & PHYSICAL_ACCOUNT_D6.pdf — Strictly 1 page, visual tables, zero truncation (H15, R8-F06, L8)
    for phys_stem in ("PHYSICAL_ACCOUNT", "PHYSICAL_ACCOUNT_D6"):
        md_p = docs_dir / f"{phys_stem}.md"
        pdf_p = docs_dir / f"{phys_stem}.pdf"
        if md_p.exists():
            render_markdown_document_to_pdf(
                md_p,
                pdf_p,
                max_pages=1,
            )
            if not verify_pdf_contains_markdown_lines(md_p, pdf_p):
                raise ValueError(f"{pdf_p.name} failed full-text verification against {md_p.name}")

    # 2. MEMO_SCENARIO_C.pdf & SCENARIO_C_MEMO.pdf — Strictly <= 2 pages, visual table, zero truncation
    for memo_stem in ("MEMO_SCENARIO_C", "SCENARIO_C_MEMO"):
        md_p = docs_dir / f"{memo_stem}.md"
        pdf_p = docs_dir / f"{memo_stem}.pdf"
        if md_p.exists():
            render_markdown_document_to_pdf(
                md_p,
                pdf_p,
                max_pages=2,
            )
            if not verify_pdf_contains_markdown_lines(md_p, pdf_p):
                raise ValueError(f"{pdf_p.name} failed full-text verification against {md_p.name}")

    # 3. PITCH_SLIDES.pdf & SLIDES_10.pdf — <= 10 visual slides (1 slide per page, <= 10 slides)
    for slides_stem in ("PITCH_SLIDES", "SLIDES_10"):
        md_p = docs_dir / f"{slides_stem}.md"
        pdf_p = docs_dir / f"{slides_stem}.pdf"
        if md_p.exists():
            render_pitch_slides_to_pdf(md_p, pdf_p)
            if not verify_pdf_contains_markdown_lines(md_p, pdf_p):
                raise ValueError(f"{pdf_p.name} failed full-text verification against {md_p.name}")

    # 4. DEMO_SCRIPT_90S.pdf & DEPLOYMENT_AND_BOM.pdf — Strictly <= 2 pages each
    for doc_stem in ("DEMO_SCRIPT_90S", "DEPLOYMENT_AND_BOM"):
        md_p = docs_dir / f"{doc_stem}.md"
        pdf_p = docs_dir / f"{doc_stem}.pdf"
        if md_p.exists():
            render_markdown_document_to_pdf(
                md_p,
                pdf_p,
                max_pages=2,
            )
            if not verify_pdf_contains_markdown_lines(md_p, pdf_p):
                raise ValueError(f"{pdf_p.name} failed full-text verification against {md_p.name}")

    # 5. README.pdf (and docs/README.pdf) — Strictly <= 3 pages
    readme_md_path = repo_root / "README.md"
    if readme_md_path.exists():
        render_markdown_document_to_pdf(
            readme_md_path,
            repo_root / "README.pdf",
            max_pages=3,
        )
        (docs_dir / "README.pdf").write_bytes((repo_root / "README.pdf").read_bytes())
        if not verify_pdf_contains_markdown_lines(readme_md_path, repo_root / "README.pdf"):
            raise ValueError("README.pdf failed full-text verification against README.md")

    # 6. FORECAST_AND_PUBLIC_DATA.pdf — Strictly <= 2 pages
    fcst_stem = "FORECAST_AND_PUBLIC_DATA"
    md_p = docs_dir / f"{fcst_stem}.md"
    pdf_p = docs_dir / f"{fcst_stem}.pdf"
    if md_p.exists():
        render_markdown_document_to_pdf(
            md_p,
            pdf_p,
            max_pages=2,
        )
        if not verify_pdf_contains_markdown_lines(md_p, pdf_p):
            raise ValueError(f"{pdf_p.name} failed full-text verification against {md_p.name}")


if __name__ == "__main__":
    generate_all_deliverable_pdfs(Path(__file__).resolve().parents[2])

