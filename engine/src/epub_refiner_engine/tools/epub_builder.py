"""标准 EPUB3 生成器、沙盒安全解压（防 Zip Slip）与 HTML/XSS 清洗。"""

from __future__ import annotations

import html
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

from epub_refiner_engine.tools.txt_tools import ParsedChapter

_BASE_CSS_BODY = """\
@charset "utf-8";
body {{
  font-family: {font_family};
  font-size: {font_size};
  line-height: {line_height};
  margin: 0 auto;
  padding: 1.2em 1.4em;
  text-align: justify;
  color: inherit;
}}
h1.volume-title {{
  font-size: 1.45em;
  text-align: center;
  margin: 2em 0 1.2em;
  page-break-before: always;
}}
h2.chapter-title {{
  font-size: 1.28em;
  font-weight: bold;
  margin: 1.4em 0 1em;
  padding-bottom: 0.35em;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}}
p {{
  text-indent: 2em;
  margin: 0.45em 0;
}}
.cover-wrap {{
  text-align: center;
  margin: 0;
  padding: 0;
}}
.cover-wrap img {{
  max-width: 100%;
  max-height: 98vh;
}}
"""

# 仅声明可免费商用的开源字体（SIL OFL）与标准通用字体族，零商业授权风险
STYLE_PRESET_CONFIG: dict[str, dict[str, str]] = {
    "classic": {
        "font_family": '"LXGW WenKai", "Noto Serif CJK SC", "Source Han Serif SC", serif',
        "font_size": "1em",
        "line_height": "1.85",
    },
    "modern": {
        "font_family": '"Noto Sans CJK SC", "Source Han Sans SC", "LXGW WenKai", sans-serif',
        "font_size": "1em",
        "line_height": "1.8",
    },
    "comfort": {
        "font_family": '"LXGW WenKai", "Noto Serif CJK SC", "Source Han Serif SC", serif',
        "font_size": "1.15em",
        "line_height": "2.05",
    },
}


def get_ebook_css(style_preset: str = "classic") -> str:
    cfg = STYLE_PRESET_CONFIG.get(style_preset, STYLE_PRESET_CONFIG["classic"])
    return _BASE_CSS_BODY.format(**cfg)


EBOOK_CSS = get_ebook_css("classic")


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """安全解压 ZIP/EPUB，严格防御 Zip Slip 路径穿越。"""
    dest_resolved = dest_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            target = (dest_resolved / member.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise ValueError(f"检测到不安全的压缩包路径（Zip Slip）: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                dst.write(src.read())


def sanitize_xhtml_fragment(raw_html: str) -> str:
    """清除危险脚本标签与事件属性，防止预览或阅读器 XSS。"""
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<iframe\b[^>]*>.*?</iframe>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\son[a-z]+\s*=\s*(['\"]).*?\1", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    try:
        parser = etree.HTMLParser(recover=True)
        doc = etree.fromstring(cleaned.encode("utf-8"), parser)
        if doc is not None:
            body = doc.find(".//body")
            if body is not None:
                return "".join(
                    etree.tostring(child, encoding="unicode", method="xml") for child in body
                )
    except etree.Error:
        pass
    return cleaned


def generate_svg_cover(title: str, author: str) -> str:
    safe_title = html.escape(title[:28] or "未命名书籍")
    safe_author = html.escape(author[:20] or "佚名")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1200" viewBox="0 0 800 1200">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e293b"/>
      <stop offset="100%" stop-color="#0f172a"/>
    </linearGradient>
  </defs>
  <rect width="800" height="1200" fill="url(#bg)"/>
  <rect x="48" y="48" width="704" height="1104" fill="none" stroke="#94a3b8" stroke-width="2" stroke-opacity="0.45"/>
  <line x1="120" y1="360" x2="680" y2="360" stroke="#38bdf8" stroke-width="4"/>
  <text x="400" y="470" text-anchor="middle" fill="#f8fafc" font-size="52" font-family="sans-serif" font-weight="bold">{safe_title}</text>
  <text x="400" y="570" text-anchor="middle" fill="#cbd5e1" font-size="30" font-family="sans-serif">{safe_author}</text>
  <text x="400" y="1060" text-anchor="middle" fill="#64748b" font-size="22" font-family="sans-serif">EPUB Refiner · 精排典藏版</text>
</svg>
"""


def build_epub3_from_chapters(
    output_path: Path,
    title: str,
    author: str,
    language: str,
    chapters: list[ParsedChapter],
    style_preset: str = "classic",
) -> Path:
    """将清洗后的章节列表打包为符合 EPUB3 规范的 .epub 文件（原子写入）。"""
    book_uuid = str(uuid.uuid4())
    modified_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_title = html.escape(title or "未命名书籍")
    safe_author = html.escape(author or "佚名")
    safe_lang = html.escape(language or "zh-CN")

    # 准备临时目标再原子替换
    tmp_path = output_path.with_suffix(".epub.tmp")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(tmp_path, "w") as zf:
        # 1. mimetype 必须是第一项且不压缩
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. META-INF/container.xml
        container_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n"
        )
        zf.writestr("META-INF/container.xml", container_xml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

        # 3. 样式与封面
        zf.writestr(
            "OEBPS/styles/main.css",
            get_ebook_css(style_preset).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr(
            "OEBPS/images/cover.svg",
            generate_svg_cover(title, author).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        cover_xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{safe_lang}">\n'
            "<head>\n"
            f"  <title>{safe_title} - 封面</title>\n"
            '  <link rel="stylesheet" type="text/css" href="styles/main.css"/>\n'
            "</head>\n"
            '<body class="cover-wrap">\n'
            '  <div><img src="images/cover.svg" alt="封面"/></div>\n'
            "</body>\n"
            "</html>\n"
        )
        zf.writestr("OEBPS/cover.xhtml", cover_xhtml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

        # 4. 章节 XHTML
        manifest_items: list[str] = [
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '    <item id="css" href="styles/main.css" media-type="text/css"/>',
            '    <item id="cover-img" href="images/cover.svg" media-type="image/svg+xml" properties="cover-image"/>',
            '    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine_items: list[str] = ['    <itemref idref="cover"/>']
        nav_lis: list[str] = []
        ncx_navpoints: list[str] = []

        last_volume: str | None = None
        for idx, ch in enumerate(chapters, start=1):
            fname = f"text/ch_{idx:04d}.xhtml"
            item_id = f"ch_{idx:04d}"
            vol_heading = ""
            if ch.volume and ch.volume != last_volume:
                vol_heading = f'  <h1 class="volume-title">{html.escape(ch.volume)}</h1>\n'
                last_volume = ch.volume

            paras_html = "\n".join(f"  <p>{html.escape(p)}</p>" for p in ch.paragraphs)
            ch_title_esc = html.escape(ch.title)
            ch_xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                "<!DOCTYPE html>\n"
                f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{safe_lang}">\n'
                "<head>\n"
                f"  <title>{ch_title_esc}</title>\n"
                '  <link rel="stylesheet" type="text/css" href="../styles/main.css"/>\n'
                "</head>\n"
                "<body>\n"
                f"{vol_heading}"
                f'  <h2 class="chapter-title">{ch_title_esc}</h2>\n'
                f"{paras_html}\n"
                "</body>\n"
                "</html>\n"
            )
            zf.writestr(f"OEBPS/{fname}", ch_xhtml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
            manifest_items.append(
                f'    <item id="{item_id}" href="{fname}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'    <itemref idref="{item_id}"/>')
            nav_lis.append(f'      <li><a href="{fname}">{ch_title_esc}</a></li>')
            ncx_navpoints.append(
                f'    <navPoint id="np_{idx}" playOrder="{idx}">\n'
                f"      <navLabel><text>{ch_title_esc}</text></navLabel>\n"
                f'      <content src="{fname}"/>\n'
                "    </navPoint>"
            )

        # 5. nav.xhtml (EPUB3 目录)
        nav_xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{safe_lang}">\n'
            "<head>\n"
            f"  <title>{safe_title} - 目录</title>\n"
            '  <link rel="stylesheet" type="text/css" href="styles/main.css"/>\n'
            "</head>\n"
            "<body>\n"
            '  <nav epub:type="toc" id="toc">\n'
            "    <h1>目录</h1>\n"
            "    <ol>\n"
            + "\n".join(nav_lis)
            + "\n    </ol>\n"
            "  </nav>\n"
            "</body>\n"
            "</html>\n"
        )
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

        # 6. toc.ncx (兼容旧阅读器)
        toc_ncx = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            "  <head>\n"
            f'    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>\n'
            '    <meta name="dtb:depth" content="1"/>\n'
            '    <meta name="dtb:totalPageCount" content="0"/>\n'
            '    <meta name="dtb:maxPageNumber" content="0"/>\n'
            "  </head>\n"
            f"  <docTitle><text>{safe_title}</text></docTitle>\n"
            "  <navMap>\n"
            + "\n".join(ncx_navpoints)
            + "\n  </navMap>\n"
            "</ncx>\n"
        )
        zf.writestr("OEBPS/toc.ncx", toc_ncx.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

        # 7. content.opf
        content_opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" '
            f'xml:lang="{safe_lang}">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="BookId">urn:uuid:{book_uuid}</dc:identifier>\n'
            f"    <dc:title>{safe_title}</dc:title>\n"
            f"    <dc:creator>{safe_author}</dc:creator>\n"
            f"    <dc:language>{safe_lang}</dc:language>\n"
            f'    <meta property="dcterms:modified">{modified_iso}</meta>\n'
            "  </metadata>\n"
            "  <manifest>\n"
            + "\n".join(manifest_items)
            + "\n  </manifest>\n"
            '  <spine toc="ncx">\n'
            + "\n".join(spine_items)
            + "\n  </spine>\n"
            "</package>\n"
        )
        zf.writestr("OEBPS/content.opf", content_opf.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

    tmp_path.replace(output_path)
    return output_path
