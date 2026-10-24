import json
import sys
from pypdf import PdfReader


def main():
    reader = PdfReader(sys.argv[1])
    if len(reader.pages) > 300:
        raise ValueError("PDF 超过 300 页处理上限")
    pages = []
    total = 0
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        total += len(text)
        if total > 2_000_000:
            raise ValueError("提取文本超过处理上限")
        pages.append({"page": i + 1, "text": text})
    print(json.dumps(pages, ensure_ascii=True))


if __name__ == "__main__":
    main()
