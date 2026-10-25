"""High-fidelity PDF parsing: text, tables, embedded images and page renderings."""

import html as html_lib
import json
import os
import shutil
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
            text = page.get_text("text", sort=True) or ""
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
            if images or tables or has_vector:
                render = f"p{number}-page.jpg"
                try:
                    pix = page.get_pixmap(dpi=settings["render_dpi"], alpha=False)
                    (assets / render).write_bytes(
                        pix.tobytes("jpeg", jpg_quality=82)
                    )
                except Exception:
                    render = ""

            pages.append(
                {
                    "page": number,
                    "text": text,
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
    print(json.dumps(pages, ensure_ascii=True))


if __name__ == "__main__":
    main()
