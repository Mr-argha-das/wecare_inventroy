"""PDF twin of the A4 billing document.

The renderer below paints the exact geometry described by
``static/css/invoice_a4.css`` (same millimetres, same font sizes, same
colours), so a downloaded PDF and a browser print of the same document are
visually identical.

Text is drawn with the PDF core font *Helvetica*, whose glyph metrics are
identical to Arial (the font the HTML uses), so line breaks land in the same
place.  Characters outside Latin-1 (the rupee sign, for example) are drawn
with the bundled DejaVu Sans font, which keeps the output correct without
losing Arial metrics for the rest of the text.
"""
from __future__ import annotations

import io
from pathlib import Path

from fpdf import FPDF

from ..config import UPLOAD_DIR
from . import invoice_layout as L

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
SYSTEM_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SYSTEM_FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

PX = 25.4 / 96.0            # 1 CSS pixel in mm
PAD_CELL = L.PAD_CELL       # table / band horizontal padding (5px)


def px(value: float) -> float:
    """CSS pixels -> millimetres."""
    return value * PX


def pt(size_px: float) -> float:
    """CSS pixels -> PDF points (1px = 0.75pt)."""
    return size_px * 0.75


def hex_rgb(value: str, fallback: tuple[int, int, int] = (0, 141, 9)) -> tuple[int, int, int]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return fallback
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return fallback


def _upload(name: str) -> Path | None:
    name = str(name or "").strip()
    if not name:
        return None
    path = Path(UPLOAD_DIR) / name
    return path if path.exists() else None


class InvoicePDF(FPDF):
    """A4 page painter for the shared billing-document model."""

    def __init__(self, doc: dict):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc = doc
        self.c = doc.get("company", {}) or {}
        self.set_auto_page_break(False)
        self.set_margins(L.PAD_X, L.PAD_TOP, L.PAD_X)
        self.c_margin = 0            # we position every string ourselves
        self.set_title(f"{doc.get('title', 'Invoice')} {doc.get('doc_number', '')}".strip())
        self.green = hex_rgb(self.c.get("invoice_color"), (0, 141, 9))
        self.rule = hex_rgb(self.c.get("rule_color"), (10, 143, 18))
        self.title_rgb = hex_rgb(self.c.get("title_color"), (7, 133, 14))
        self.link_rgb = hex_rgb(self.c.get("link_color"), (39, 165, 213))
        self.uni = self._register_unicode_font()

    # -- fonts ------------------------------------------------------------
    def _register_unicode_font(self) -> str | None:
        regular = FONT_DIR / "DejaVuSans.ttf"
        bold = FONT_DIR / "DejaVuSans-Bold.ttf"
        if not regular.exists():
            regular = SYSTEM_FONT
        if not bold.exists():
            bold = SYSTEM_FONT_BOLD
        try:
            self.add_font("uni", "", str(regular))
            self.add_font("uni", "B", str(bold) if Path(bold).exists() else str(regular))
            return "uni"
        except Exception:
            return None

    def _use(self, family: str, bold: bool, size_px: float) -> None:
        self.set_font(family, "B" if bold else "", pt(size_px))

    def _runs(self, text: str) -> list[tuple[str, str]]:
        """Split text into (font_family, chunk) runs; Latin-1 -> Helvetica."""
        out: list[tuple[str, str]] = []
        current, family = "", None
        for ch in str(text or ""):
            try:
                ch.encode("latin-1")
                fam = "helvetica"
            except UnicodeEncodeError:
                fam = self.uni or "helvetica"
                if fam == "helvetica":
                    ch = "?"
            if family is None:
                family, current = fam, ch
            elif fam == family:
                current += ch
            else:
                out.append((family, current))
                family, current = fam, ch
        if family is not None:
            out.append((family, current))
        return out

    def width_of(self, text: str, size_px: float, bold: bool = False) -> float:
        total = 0.0
        for family, chunk in self._runs(text):
            self._use(family, bold, size_px)
            total += self.get_string_width(chunk)
        return total

    # -- primitives -------------------------------------------------------
    def write_line(self, x: float, y: float, w: float, h: float, text: str, *,
                   size_px: float = 10, bold: bool = False, align: str = "L",
                   color: tuple[int, int, int] = (17, 17, 17)) -> None:
        """One line of text inside the box (x, y, w, h), vertically centred."""
        text = "" if text is None else str(text)
        if not text:
            return
        self.set_text_color(*color)
        runs = self._runs(text)
        total = 0.0
        widths = []
        for family, chunk in runs:
            self._use(family, bold, size_px)
            cw = self.get_string_width(chunk)
            widths.append(cw)
            total += cw
        if align == "R":
            cursor = x + w - total
        elif align == "C":
            cursor = x + (w - total) / 2
        else:
            cursor = x
        for (family, chunk), cw in zip(runs, widths):
            self._use(family, bold, size_px)
            self.set_xy(cursor, y)
            self.cell(cw, h, chunk, align="L")
            cursor += cw

    def wrap(self, text: str, width: float, size_px: float, bold: bool = False) -> list[str]:
        """Greedy word wrap using real glyph metrics (mirrors the browser)."""
        lines: list[str] = []
        for paragraph in str(text or "").splitlines() or [""]:
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if not current or self.width_of(candidate, size_px, bold) <= width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
                while self.width_of(current, size_px, bold) > width and len(current) > 1:
                    cut = len(current) - 1              # hard-break a long word
                    while cut > 1 and self.width_of(current[:cut], size_px, bold) > width:
                        cut -= 1
                    lines.append(current[:cut])
                    current = current[cut:]
            lines.append(current)
        return lines or [""]

    def write_block(self, x: float, y: float, w: float, text: str, *, size_px: float = 10,
                    line_h: float = 0.0, bold: bool = False,
                    color: tuple[int, int, int] = (17, 17, 17), max_lines: int = 0) -> float:
        """Multi-line text; returns the height used."""
        line_h = line_h or px(size_px * 1.5)
        lines = self.wrap(text, w, size_px, bold)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][: max(0, len(lines[-1]) - 1)] + "…"
        for i, line in enumerate(lines):
            self.write_line(x, y + i * line_h, w, line_h, line, size_px=size_px, bold=bold, color=color)
        return len(lines) * line_h

    def fill(self, x: float, y: float, w: float, h: float, rgb: tuple[int, int, int],
             radius: float = 0.0) -> None:
        self.set_fill_color(*rgb)
        self.set_draw_color(*rgb)
        if radius:
            try:
                self.rect(x, y, w, h, style="F", round_corners=True, corner_radius=radius)
                return
            except TypeError:
                pass
        self.rect(x, y, w, h, style="F")

    def hline(self, x: float, y: float, w: float, rgb: tuple[int, int, int], weight: float = px(1)) -> None:
        self.set_draw_color(*rgb)
        self.set_line_width(weight)
        self.line(x, y, x + w, y)

    def place_image(self, path: Path, x: float, y: float, w: float, h: float, align: str = "L") -> None:
        """object-fit: contain, anchored left/right-top inside the given box."""
        try:
            self.image(str(path), x=x, y=y, w=w, h=h, keep_aspect_ratio=True)
        except Exception:
            try:
                self.image(str(path), x=x, y=y, h=h)
            except Exception:
                pass

    # -- sections ---------------------------------------------------------
    def draw_header(self) -> None:
        c = self.c
        top = L.PAD_TOP
        logo = _upload(c.get("logo"))
        if logo:
            self.place_image(logo, L.PAD_X, top + 2, 19, 28)
        x = L.PAD_X + L.CONTENT_W - 105
        y = top
        self.write_line(x, y, 105, px(16 * 1.3), c.get("brand_name", ""), size_px=16, bold=True, align="R")
        y += px(16 * 1.3) + px(3)
        for line in c.get("header_lines", []):
            for chunk in self.wrap(line, 105, 9):
                self.write_line(x, y, 105, px(9 * 1.3), chunk, size_px=9, align="R", color=(17, 17, 17))
                y += px(9 * 1.3)
        if str(c.get("gstin") or "").strip():
            y += px(2)
            self.write_line(x, y, 105, px(9 * 1.3), f"GST Number : {c['gstin']}", size_px=9, align="R")

    def draw_title(self) -> None:
        y = L.PAD_TOP + L.HEADER_H
        self.fill(L.PAD_X, y, L.CONTENT_W, L.RULE_H, self.rule)
        y += L.RULE_H
        self.write_line(L.PAD_X, y, L.CONTENT_W, L.TITLE_H, self.doc.get("title", ""),
                        size_px=13, bold=True, align="C", color=self.title_rgb)
        self.hline(L.PAD_X, y + L.TITLE_H, L.CONTENT_W, self.rule, px(1))

    def draw_meta(self) -> None:
        doc = self.doc
        y0 = L.PAD_TOP + L.HEADER_H + L.RULE_H + L.TITLE_H + L.META_PAD_TOP
        # Bill To (left, 50%)
        left_w = L.CONTENT_W * 0.5
        y = y0
        bt = doc["bill_to"]
        self.write_line(L.PAD_X, y, left_w, px(11 * 1.75), bt.get("heading", "Bill To"), size_px=11, bold=True)
        y += px(11 * 1.75)
        if bt.get("name"):
            self.write_line(L.PAD_X, y, left_w, px(11 * 1.75), bt["name"], size_px=11, bold=True)
            y += px(11 * 1.75)
        for line in bt.get("lines", []):
            for chunk in self.wrap(line, left_w, 10):
                self.write_line(L.PAD_X, y, left_w, px(10 * 1.75), chunk, size_px=10)
                y += px(10 * 1.75)
        # Invoice Details (right, 42%)
        right_w = L.CONTENT_W * 0.42
        rx = L.PAD_X + L.CONTENT_W - right_w
        y = y0
        det = doc["details"]
        self.write_line(rx, y, right_w, px(11 * 1.55), det.get("heading", ""), size_px=11, bold=True, align="R")
        y += px(11 * 1.55) + px(2)
        for label, value in det.get("rows", []):
            self.write_line(rx, y, right_w, px(10 * 1.55), f"{label}: {value}", size_px=10, align="R")
            y += px(10 * 1.55)

    def _columns(self) -> list[tuple[dict, float, float]]:
        cols = self.doc["table"]["columns"]
        out, x = [], L.PAD_X
        for col in cols:
            w = L.CONTENT_W * float(col.get("w", 100 / max(1, len(cols)))) / 100.0
            out.append((col, x, w))
            x += w
        return out

    @staticmethod
    def _align(cls: str) -> str:
        if "amount" in cls or "price" in cls:
            return "R"
        if "date" in cls or "days" in cls or "mid" in cls:
            return "C"
        return "L"

    def draw_table(self, rows: list[list[dict]]) -> float:
        y = L.table_top()
        cols = self._columns()
        # header band
        self.fill(L.PAD_X, y, L.CONTENT_W, L.TABLE_HEAD_H, self.green)
        for col, x, w in cols:
            align = self._align(col.get("cls", ""))
            self.write_line(x + PAD_CELL, y, w - 2 * PAD_CELL, L.TABLE_HEAD_H, col["label"],
                            size_px=10, bold=True, align=align, color=(255, 255, 255))
            if (col, x, w) != cols[-1]:
                self.set_draw_color(255, 255, 255)
                self.set_line_width(px(1))
                self.line(x + w, y, x + w, y + L.TABLE_HEAD_H)
        y += L.TABLE_HEAD_H
        # body
        meta = [c for c, _, _ in cols]
        for row in rows:
            main_lines, sub_lines, h = L.row_metrics(row, meta)
            for (col, x, w), data in zip(cols, row):
                align = self._align(col.get("cls", ""))
                inner = w - 2 * PAD_CELL
                text_lines = self.wrap(data.get("text", ""), inner, 10)[:main_lines]
                for i, line in enumerate(text_lines):
                    self.write_line(x + PAD_CELL, y + i * L.TABLE_LINE_H, inner, L.TABLE_ROW_H,
                                    line, size_px=10, align=align, color=(17, 17, 17))
                if data.get("sub") and sub_lines:
                    sub_y = y + L.TABLE_ROW_H + (main_lines - 1) * L.TABLE_LINE_H - px(1)
                    for i, line in enumerate(self.wrap(data["sub"], inner, 8.5)[:sub_lines]):
                        self.write_line(x + PAD_CELL, sub_y + i * L.TABLE_SUB_H, inner,
                                        L.TABLE_SUB_H, line, size_px=8.5, align=align,
                                        color=(85, 85, 85))
            self.hline(L.PAD_X, y + h, L.CONTENT_W, (119, 119, 119), px(1))
            y += h
        return y

    def band(self, x: float, y: float, w: float, text: str) -> float:
        self.fill(x, y, w, L.BAND_H, self.green)
        self.write_line(x + PAD_CELL, y + px(3), w - 2 * PAD_CELL, px(10 * 1.15), text,
                        size_px=10, bold=True, color=(255, 255, 255))
        return y + L.BAND_H

    def draw_lower(self, y_table_bottom: float) -> None:
        doc = self.doc
        y0 = y_table_bottom + L.LOWER_TOP
        left_x = L.PAD_X
        right_x = L.PAD_X + L.LEFT_COL_W + L.LOWER_GAP

        # ---- left column: words / payment type / terms / bank ----------
        y = y0
        for block in doc.get("blocks", []):
            y = self.band(left_x, y, L.LEFT_COL_W, block["title"])
            body_h = self.write_block(left_x + PAD_CELL, y + px(4), L.LEFT_COL_W - 2 * PAD_CELL,
                                      block["body"], size_px=10, line_h=L.BODY_LINE_H)
            y += max(L.BODY_MIN_H, L.BODY_PAD + body_h) + L.BLOCK_GAP

        bank = doc.get("bank", {}) or {}
        if bank.get("show"):
            y += L.BANK_TOP
            y = self.band(left_x, y, L.LEFT_COL_W, "Bank Details")
            content_y = y + L.BANK_PAD_TOP
            text_x = left_x
            qr = _upload(bank.get("qr"))
            if qr:
                self.place_image(qr, left_x, content_y, L.QR_W, L.QR_H)
                text_x = left_x + L.QR_W + L.QR_GAP
            ty = content_y + px(1)
            for line in bank.get("lines", []):
                for chunk in self.wrap(line, left_x + L.LEFT_COL_W - text_x, 9):
                    self.write_line(text_x, ty, left_x + L.LEFT_COL_W - text_x, L.BANK_LINE_H,
                                    chunk, size_px=9)
                    ty += L.BANK_LINE_H
            y = max(ty, content_y + (L.QR_H if qr else 0))
        left_bottom = y

        # ---- right column: amounts panel --------------------------------
        y = self.band(right_x, y0, L.RIGHT_COL_W, doc.get("amounts_title", "Amounts"))
        for row in doc.get("amounts", []):
            bold = bool(row.get("strong"))
            color = (85, 85, 85) if row.get("muted") else (17, 17, 17)
            self.write_line(right_x + PAD_CELL, y, L.RIGHT_COL_W - 2 * PAD_CELL, L.AMOUNT_ROW_H,
                            row["label"], size_px=10, bold=bold, color=color)
            self.write_line(right_x + PAD_CELL, y, L.RIGHT_COL_W - 2 * PAD_CELL, L.AMOUNT_ROW_H,
                            row["value"], size_px=10, bold=bold, align="R", color=color)
            self.hline(right_x, y + L.AMOUNT_ROW_H, L.RIGHT_COL_W, (119, 119, 119), px(1))
            y += L.AMOUNT_ROW_H

        sign = doc.get("signature", {}) or {}
        if sign.get("show"):
            y += px(4)
            img = _upload(sign.get("image"))
            if img:
                self.place_image(img, right_x + L.RIGHT_COL_W - 40, y, 40, 14, align="R")
                y += 14
            self.write_line(right_x, y, L.RIGHT_COL_W, px(9 * 1.5),
                            f"For {self.c.get('brand_name', '')}", size_px=9, align="R")
            y += px(9 * 1.5)
            self.write_line(right_x, y, L.RIGHT_COL_W, px(9 * 1.5), sign.get("name", ""),
                            size_px=9, bold=True, align="R")
            y += px(9 * 1.5)

        y = max(left_bottom, y)
        if doc.get("notes"):
            y += L.BLOCK_GAP
            y += self.write_block(L.PAD_X, y, L.CONTENT_W, "Note: " + str(doc["notes"]),
                                  size_px=9, line_h=L.NOTE_LINE_H)
        if str(self.c.get("declaration") or "").strip():
            y += L.BLOCK_GAP
            self.write_block(L.PAD_X, y, L.CONTENT_W, str(self.c["declaration"]),
                             size_px=8.5, line_h=L.NOTE_LINE_H, color=(85, 85, 85))

    def draw_footer(self, page: dict) -> None:
        c = self.c
        bottom = L.PAGE_H - L.FOOTER_BOTTOM          # 286mm
        if str(c.get("footer_text") or "").strip():
            h = px(8 * 1.3)
            offset = 12 if c.get("show_page_number", True) else 0   # clear the page chip
            self.write_line(L.PAD_X + offset, bottom - 1 - h, L.CONTENT_W / 2, h,
                            str(c["footer_text"]), size_px=8, bold=True, color=self.link_rgb)
        img = _upload(c.get("footer_image"))
        if img:
            self.place_image(img, L.PAD_X + L.CONTENT_W - 36, bottom - 14, 36, 14, align="R")
        if c.get("show_page_number", True):
            box_x, box_y = L.PAD_X - 1, bottom + 4 - 10
            self.fill(box_x, box_y, 10, 10, (136, 136, 136), radius=px(2))
            self.write_line(box_x, box_y, 10, 10, str(page.get("index", 1)),
                            size_px=10, align="C", color=(255, 255, 255))


def render(doc: dict) -> bytes:
    """Render the shared document model to PDF bytes."""
    pdf = InvoicePDF(doc)
    for page in doc.get("pages") or [{"index": 1, "rows": [], "show_lower": True}]:
        pdf.add_page()
        pdf.draw_header()
        pdf.draw_title()
        pdf.draw_meta()
        bottom = pdf.draw_table(page.get("rows", []))
        if page.get("show_lower"):
            pdf.draw_lower(bottom)
        pdf.draw_footer(page)
    return bytes(io.BytesIO(pdf.output()).getvalue())
