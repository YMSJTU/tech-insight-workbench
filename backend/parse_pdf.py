"""High-fidelity PDF parsing: text, tables, embedded images and page renderings."""

import html as html_lib
import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

import pymupdf

from .config import pdf_settings

MAX_PAGES = 300
MAX_TEXT = 2_000_000
MAX_IMAGE_BYTES = 40 * 1024 * 1024


def table_html(rows):
    body = []
    for row in rows:
        cells = "".join(
            "<td>" + html_lib.escape("" if cell is None else str(cell)) + "</td>"
            for cell in row
        )
        body.append("<tr>" + cells + "</tr>")
    return "<table>" + "".join(body) + "</table>"


def _table_cell(value):
    return (
        ("" if value is None else str(value))
        .replace("|", "\\|")
        .replace("\n", " ")
        .strip()
    )


def table_markdown(rows):
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [
        list(row) + [""] * (width - len(row)) for row in rows
    ]
    lines = [
        "| " + " | ".join(_table_cell(cell) for cell in normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(_table_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def usable_table(rows):
    """Reject figure boxes and text blocks that look like a huge one-cell table."""
    if len(rows) < 2:
        return False
    width = max(len(row) for row in rows)
    if width < 2:
        return False
    cells = [
        ("" if cell is None else str(cell)).strip()
        for row in rows
        for cell in row
    ]
    non_empty = [cell for cell in cells if cell]
    if len(non_empty) < max(2, len(cells) // 2):
        return False
    lengths = [len(cell) for cell in non_empty]
    return max(lengths, default=0) <= 300 and (
        sum(lengths) / len(lengths) if lengths else 0
    ) <= 120


def page_markdown_blocks(page, tables, page_number):
    """Build layout-aware Markdown blocks with heading and paragraph heuristics."""
    rects = [pymupdf.Rect(table["bbox"]) for table in tables]
    visual_rects = []
    try:
        area = page.rect.width * page.rect.height
        visual_rects = [
            rect
            for rect in page.cluster_drawings()
            if rect.width * rect.height >= area * 0.01
        ]
    except Exception:
        visual_rects = []
    for image in page.get_images(full=True):
        try:
            visual_rects.extend(page.get_image_rects(image[0]))
        except Exception:
            pass
    raw = []
    sizes = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        rect = pymupdf.Rect(block["bbox"])
        if any(rect.intersects(table_rect) for table_rect in rects):
            continue
        center = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        if any(visual_rect.contains(center) for visual_rect in visual_rects):
            continue
        lines = []
        block_size = 0.0
        bold = False
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            value = "".join(span.get("text", "") for span in spans).strip()
            if not value:
                continue
            lines.append(value)
            for span in spans:
                text = span.get("text", "")
                if not text.strip():
                    continue
                size = float(span.get("size") or 0)
                sizes.extend([size] * len(text))
                block_size = max(block_size, size)
                if span.get("flags", 0) & 16 or "bold" in (
                    span.get("font") or ""
                ).lower():
                    bold = True
        if not lines:
            continue
        value = "\n".join(lines)
        value = re.sub(r"-\s*\n\s*", "", value)
        value = re.sub(r"\s*\n\s*", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        if not value or re.fullmatch(r"\d{1,3}", value):
            continue
        if re.match(
            r"^(?:arxiv:\s*\d|preprint\b|under review\b|accepted\b)",
            value,
            re.I,
        ):
            continue
        if re.match(
            r"^(?:permission to make|acm reference format|acm isbn|copyright\b|"
            r"©|authorized licensed|this work is licensed|"
            r"[∗*]\s*corresponding author)",
            value,
            re.I,
        ):
            continue
        raw.append({"text": value, "size": block_size, "bold": bold})

    body = statistics.median(sizes) if sizes else 9.0
    page_max = max((item["size"] for item in raw), default=body)
    result = []
    title_used = False
    for item in raw:
        value = item["text"]
        level = 0
        if (
            page_number == 1
            and not title_used
            and len(value) >= 10
            and item["size"] >= page_max * 0.85
        ):
            level = 1
            title_used = True
        elif re.match(r"^\d+(?:\.\d+)*\s+\S", value) and len(value) <= 120:
            level = 2
        elif (
            len(value) <= 120
            and item["size"] >= body * 1.35
            and not value.endswith((".", ",", ";"))
        ):
            level = 2
        elif (
            item["bold"]
            and len(value) <= 80
            and item["size"] >= body * 1.02
            and not value.endswith((".", ",", ";"))
        ):
            level = 3
        if value.lstrip().startswith(("•", "·")):
            items = [
                part.strip()
                for part in re.split(r"\s*[•·]\s*", value)
                if part.strip()
            ]
            result.append("\n".join("- " + part for part in items))
        elif level:
            result.append("#" * level + " " + value)
        else:
            result.append(value)
    return result


def main():
    path = Path(sys.argv[1])
    assets = Path(str(path) + ".assets")
    shutil.rmtree(assets, ignore_errors=True)
    assets.mkdir(parents=True, exist_ok=True)
    settings = pdf_settings()
    saved_stdout = os.dup(1)
    sys.stdout.flush()
    os.dup2(2, 1)
    try:
        doc = pymupdf.open(path)
        if len(doc) > MAX_PAGES:
            raise ValueError("PDF 超过 300 页处理上限")

        pages = []
        total_text = 0
        seen_images = {}
        image_bytes = 0
        for index, page in enumerate(doc):
            number = index + 1
            text = page.get_text("text") or ""
            total_text += len(text)
            if total_text > MAX_TEXT:
                raise ValueError("提取文本超过处理上限")

            tables = []
            try:
                finder = page.find_tables()
                for table in finder.tables[:10]:
                    rows = [
                        ["" if cell is None else str(cell) for cell in row]
                        for row in table.extract()[:200]
                    ]
                    if not usable_table(rows):
                        continue
                    tables.append(
                        {
                            "bbox": list(table.bbox),
                            "rows": rows,
                            "html": table_html(rows),
                        }
                    )
            except Exception:
                tables = []

            images = []
            for image in page.get_images(full=True)[:50]:
                xref = image[0]
                if xref in seen_images:
                    name, width, height = seen_images[xref]
                else:
                    try:
                        info = doc.extract_image(xref)
                    except Exception:
                        continue
                    data = info.get("image") or b""
                    if not data:
                        continue
                    if image_bytes + len(data) > MAX_IMAGE_BYTES:
                        continue
                    image_bytes += len(data)
                    ext = (info.get("ext") or "png").lower()
                    if ext not in {"png", "jpg", "jpeg"}:
                        try:
                            data = pymupdf.Pixmap(data).tobytes("png")
                        except Exception:
                            continue
                        ext = "png"
                    name = f"p{number}-x{xref}.{ext}"
                    (assets / name).write_bytes(data)
                    width = info.get("width") or 0
                    height = info.get("height") or 0
                    seen_images[xref] = (name, width, height)
                try:
                    rects = page.get_image_rects(xref)
                    bbox = list(rects[0]) if rects else []
                except Exception:
                    bbox = []
                images.append(
                    {
                        "name": name,
                        "width": width,
                        "height": height,
                        "bbox": bbox,
                    }
                )

            render = ""
            try:
                has_vector = bool(page.get_drawings())
            except Exception:
                has_vector = False
            figure_caption = bool(
                re.search(r"\b(?:Figure|Fig\.)\s*\d+", text, re.I)
            )
            if images or (has_vector and figure_caption):
                render = f"p{number}-page.jpg"
                try:
                    pix = page.get_pixmap(dpi=settings["render_dpi"], alpha=False)
                    (assets / render).write_bytes(
                        pix.tobytes("jpeg", jpg_quality=82)
                    )
                except Exception:
                    render = ""

            body_blocks = page_markdown_blocks(page, tables, number)
            if not body_blocks:
                body_blocks = [text]
            markdown_parts = [f"## Page {number}", *body_blocks]
            for table in tables:
                markdown_parts.append(table_markdown(table["rows"]))
            for image in images:
                markdown_parts.append(
                    f"![figure](assets/{image['name']})"
                )
            if render:
                markdown_parts.append(
                    f"![page layout](assets/{render})"
                )
            page_markdown = "\n\n".join(part for part in markdown_parts if part)

            pages.append(
                {
                    "page": number,
                    "text": text,
                    "markdown": page_markdown,
                    "images": images,
                    "tables": tables,
                    "render": render,
                }
            )
    except Exception:
        shutil.rmtree(assets, ignore_errors=True)
        raise
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)
    markdown = "\n\n".join(page["markdown"] for page in pages)
    print(json.dumps({"pages": pages, "markdown": markdown}, ensure_ascii=True))


if __name__ == "__main__":
    main()
