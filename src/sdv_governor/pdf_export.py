"""Pure-Python deterministic PDF 1.4 generator for deliverable documents (B7, B11, DOC-01, H15)."""

from __future__ import annotations

from pathlib import Path
import textwrap
from typing import List


def _escape_pdf_text(s: str) -> str:
    # Convert common unicode symbols and any residual LaTeX tokens to clean ASCII equivalents for Type1 Helvetica/Courier (V2-F09)
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


def write_multi_page_pdf(
    pages_lines: List[List[str]],
    out_path: Path,
    font_size: int = 9,
    leading: int = 11,
    start_y: int = 756,
    min_bottom_y: int = 24,
) -> None:
    """Write a valid PDF-1.4 file with exact page count `len(pages_lines)` and fail-closed overflow protection."""
    max_lines_per_page = max(1, (start_y - min_bottom_y) // max(1, leading))
    for page_idx, lines in enumerate(pages_lines, start=1):
        if len(lines) > max_lines_per_page:
            raise ValueError(
                f"PDF_PAGE_OVERFLOW in {out_path.name} page {page_idx}: "
                f"{len(lines)} lines > max {max_lines_per_page} lines at font_size={font_size}, leading={leading}. "
                f"Silent line truncation is forbidden (H15)."
            )

    objects: List[bytes] = []

    def add_obj(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    # Obj 1: Catalog (placeholder updated below)
    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Obj 2: Pages (placeholder updated below)
    add_obj(b"")
    # Obj 3: Font Helvetica
    font_obj_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    # Obj 4: Font Helvetica-Bold
    font_bold_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    page_obj_ids: List[int] = []
    for lines in pages_lines:
        stream_cmds: List[str] = ["BT", f"/F1 {font_size} Tf", f"36 {start_y} Td", f"{leading} TL"]
        for line in lines:
            clean = line.strip()
            is_heading = clean.startswith("#") or clean.startswith("**To:") or clean.startswith("Slide ")
            esc = _escape_pdf_text(line)
            if is_heading:
                stream_cmds.append(f"/F2 {font_size + 1} Tf ({esc}) Tj /F1 {font_size} Tf T*")
            else:
                stream_cmds.append(f"({esc}) Tj T*")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("ascii")
        content_id = add_obj(
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii") + stream_bytes + b"\nendstream"
        )
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj_id} 0 R /F2 {font_bold_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_id = add_obj(page_dict)
        page_obj_ids.append(page_id)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii")

    # Serialize PDF with xref table
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


def _wrap_markdown_lines(md_text: str, width: int = 96) -> List[str]:
    wrapped: List[str] = []
    for raw_ln in md_text.splitlines():
        if not raw_ln.strip():
            wrapped.append("")
        else:
            parts = textwrap.wrap(raw_ln, width=width, replace_whitespace=False, drop_whitespace=False)
            wrapped.extend(parts if parts else [""])
    return wrapped


def verify_pdf_contains_markdown_lines(md_path: Path, pdf_path: Path, width: int) -> bool:
    """Verify that every non-empty wrapped line of `md_path` is present in `pdf_path` (zero truncation — H15)."""
    md_text = md_path.read_text(encoding="utf-8")
    wrapped_lines = [ln for ln in _wrap_markdown_lines(md_text, width=width) if ln.strip()]
    pdf_ascii = pdf_path.read_bytes().decode("ascii", errors="ignore")
    for ln in wrapped_lines:
        esc = _escape_pdf_text(ln)
        if f"({esc}) Tj" not in pdf_ascii:
            return False
    return True


def generate_all_deliverable_pdfs(repo_root: Path) -> None:
    docs_dir = repo_root / "docs"

    # 1. PHYSICAL_ACCOUNT.pdf — Strictly 1 page, zero truncation (H15)
    phys_md = (docs_dir / "PHYSICAL_ACCOUNT.md").read_text(encoding="utf-8")
    phys_lines = _wrap_markdown_lines(phys_md, width=116)
    write_multi_page_pdf(
        [phys_lines],
        docs_dir / "PHYSICAL_ACCOUNT.pdf",
        font_size=7,
        leading=8,
        start_y=768,
        min_bottom_y=20,
    )
    if not verify_pdf_contains_markdown_lines(docs_dir / "PHYSICAL_ACCOUNT.md", docs_dir / "PHYSICAL_ACCOUNT.pdf", width=116):
        raise ValueError("PHYSICAL_ACCOUNT.pdf failed full-text verification against PHYSICAL_ACCOUNT.md")

    # 2. MEMO_SCENARIO_C.pdf — Strictly 2 pages (<= 2 pages), zero truncation
    memo_md = (docs_dir / "MEMO_SCENARIO_C.md").read_text(encoding="utf-8")
    memo_lines = _wrap_markdown_lines(memo_md, width=102)
    mid = max(1, (len(memo_lines) + 1) // 2)
    memo_pages = [memo_lines[:mid], memo_lines[mid:]]
    write_multi_page_pdf(
        memo_pages,
        docs_dir / "MEMO_SCENARIO_C.pdf",
        font_size=8,
        leading=10,
        start_y=762,
        min_bottom_y=22,
    )
    if not verify_pdf_contains_markdown_lines(docs_dir / "MEMO_SCENARIO_C.md", docs_dir / "MEMO_SCENARIO_C.pdf", width=102):
        raise ValueError("MEMO_SCENARIO_C.pdf failed full-text verification against MEMO_SCENARIO_C.md")

    # 3. PITCH_SLIDES.pdf — 8 slides (1 slide per page, <= 10 slides), zero truncation
    pitch_md = (docs_dir / "PITCH_SLIDES.md").read_text(encoding="utf-8")
    raw_slides = [s.strip() for s in pitch_md.split("---") if s.strip()]
    if len(raw_slides) > 10:
        raise ValueError(f"PITCH_SLIDES.md has {len(raw_slides)} slides > 10 maximum allowed")
    slide_pages: List[List[str]] = []
    for sl in raw_slides:
        slide_pages.append(_wrap_markdown_lines(sl, width=92))
    write_multi_page_pdf(
        slide_pages,
        docs_dir / "PITCH_SLIDES.pdf",
        font_size=9,
        leading=12,
        start_y=756,
        min_bottom_y=24,
    )


if __name__ == "__main__":
    generate_all_deliverable_pdfs(Path(__file__).resolve().parents[2])
