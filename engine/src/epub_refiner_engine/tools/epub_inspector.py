"""粗糙 EPUB 安全解包、DRM 检测、结构体检与自动重构工具。"""

from __future__ import annotations

import html
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree
from lxml import html as lxml_html

from epub_refiner_engine.models import EpubDiagnosisReport, EpubIssue
from epub_refiner_engine.tools.epub_builder import (
    EBOOK_CSS,
    generate_svg_cover,
    safe_extract_zip,
    sanitize_xhtml_fragment,
)
from epub_refiner_engine.tools.epubcheck import run_epubcheck


def _find_opf_path(extracted_dir: Path) -> Path | None:
    container = extracted_dir / "META-INF" / "container.xml"
    if container.exists():
        try:
            tree = etree.parse(str(container))
            rf = tree.find(".//{*}rootfile")
            if rf is not None and rf.get("full-path"):
                candidate = (extracted_dir / rf.get("full-path")).resolve()
                if candidate.is_relative_to(extracted_dir.resolve()) and candidate.exists():
                    return candidate
        except etree.Error:
            pass
    opfs = list(extracted_dir.rglob("*.opf"))
    return opfs[0] if opfs else None


def inspect_epub(epub_path: Path, work_dir: Path) -> EpubDiagnosisReport:
    """解包并全面体检 EPUB，输出问题清单与初始 EPUBCheck 结果。"""
    safe_extract_zip(epub_path, work_dir)
    issues: list[EpubIssue] = []

    has_drm = (work_dir / "META-INF" / "encryption.xml").exists() or (
        work_dir / "META-INF" / "rights.xml"
    ).exists()
    if has_drm:
        issues.append(
            EpubIssue(
                code="DRM_PROTECTED",
                label="检测到 DRM 加密保护",
                severity="error",
                auto_fixable=False,
                description="出于版权与合规原则，本软件不支持处理受 DRM 保护的加密电子书。",
            )
        )

    title = ""
    author = ""
    language = "zh-CN"
    has_cover = False
    spine_count = 0
    toc_count = 0

    opf_path = _find_opf_path(work_dir)
    if opf_path is None:
        issues.append(
            EpubIssue(
                code="MISSING_OPF",
                label="缺少核心包描述文件 (content.opf)",
                severity="error",
                description="无法定位 OPF 清单，需要重建标准 EPUB3 目录与清单。",
            )
        )
    else:
        try:
            tree = etree.parse(str(opf_path))
            t_el = tree.find(".//{http://purl.org/dc/elements/1.1/}title")
            a_el = tree.find(".//{http://purl.org/dc/elements/1.1/}creator")
            l_el = tree.find(".//{http://purl.org/dc/elements/1.1/}language")
            title = (t_el.text or "").strip() if t_el is not None else ""
            author = (a_el.text or "").strip() if a_el is not None else ""
            language = (l_el.text or "").strip() if l_el is not None else ""

            spine_count = len(tree.findall(".//{*}spine/{*}itemref"))
            for item in tree.findall(".//{*}manifest/{*}item"):
                props = (item.get("properties") or "").split()
                item_id = (item.get("id") or "").lower()
                if "cover-image" in props or item_id in {"cover-image", "cover_img"}:
                    has_cover = True
                if "nav" in props:
                    nav_file = (opf_path.parent / (item.get("href") or "")).resolve()
                    if nav_file.exists():
                        try:
                            nav_doc = etree.parse(str(nav_file))
                            toc_count = len(nav_doc.findall(".//{*}nav//{*}a"))
                        except etree.Error:
                            pass
        except etree.Error:
            issues.append(
                EpubIssue(
                    code="CORRUPT_OPF",
                    label="content.opf XML 语法损坏",
                    severity="error",
                    description="OPF 元数据文件无法解析，需要重新生成规范结构。",
                )
            )

    if not title:
        issues.append(
            EpubIssue(
                code="MISSING_TITLE",
                label="缺少书名元数据 (dc:title)",
                severity="warning",
                description="将从文件名推断或由您手动补充标准书名。",
            )
        )
    if not author:
        issues.append(
            EpubIssue(
                code="MISSING_AUTHOR",
                label="缺少作者元数据 (dc:creator)",
                severity="warning",
                description="建议补充作者名，方便阅读器分类管理。",
            )
        )
    if not has_cover:
        issues.append(
            EpubIssue(
                code="MISSING_COVER",
                label="缺少标准封面图",
                severity="warning",
                description="可自动生成极简矢量封面并写入 EPUB3 cover-image 属性。",
            )
        )
    if toc_count == 0 and spine_count > 0:
        issues.append(
            EpubIssue(
                code="MISSING_TOC",
                label="缺少 EPUB3 导航目录 (nav.xhtml)",
                severity="warning",
                description="将为现有正文章节重建标准目录导航。",
            )
        )

    initial_check = run_epubcheck(epub_path)
    if not initial_check.passed:
        issues.append(
            EpubIssue(
                code="EPUBCHECK_ERRORS",
                label=f"EPUBCheck 校验发现 {initial_check.fatal_count + initial_check.error_count} 处规范错误",
                severity="error",
                description="Repair Agent 将在构建后读取报错日志并循环修复，直到 0 Error 通过。",
            )
        )

    return EpubDiagnosisReport(
        source_path=str(epub_path),
        file_size_bytes=epub_path.stat().st_size,
        has_drm=has_drm,
        title=title or epub_path.stem,
        author=author or "佚名",
        language=language or "zh-CN",
        has_cover=has_cover,
        spine_count=spine_count,
        toc_count=toc_count,
        initial_check=initial_check,
        issues=issues,
    )


def repack_refined_epub(
    work_dir: Path,
    output_path: Path,
    title: str,
    author: str,
    language: str,
    fix_cover: bool = True,
    inject_clean_css: bool = True,
    sanitize_scripts: bool = True,
) -> Path:
    """将体检/修复后的解包目录规范化并重新打包为标准 EPUB3。"""
    opf_path = _find_opf_path(work_dir)
    if opf_path is None:
        raise RuntimeError("未找到 OPF 文件，无法重打包。")

    opf_dir = opf_path.parent
    tree = etree.parse(str(opf_path))
    root = tree.getroot()
    ns_opf = "http://www.idpf.org/2007/opf"
    ns_dc = "http://purl.org/dc/elements/1.1/"

    # 1. 升级为 EPUB 3.0 并补齐必需元数据
    root.set("version", "3.0")
    metadata = root.find(".//{*}metadata")
    if metadata is None:
        metadata = etree.SubElement(root, f"{{{ns_opf}}}metadata")

    def ensure_dc(tag: str, val: str, **attrs: str) -> None:
        el = metadata.find(f".//{{{ns_dc}}}{tag}")
        if el is None:
            el = etree.SubElement(metadata, f"{{{ns_dc}}}{tag}", **attrs)
        el.text = val

    if metadata.find(f".//{{{ns_dc}}}identifier") is None:
        id_attr = root.get("unique-identifier") or "BookId"
        root.set("unique-identifier", id_attr)
        ensure_dc("identifier", f"urn:uuid:{uuid.uuid4()}", id=id_attr)

    ensure_dc("title", title)
    ensure_dc("creator", author)
    ensure_dc("language", language)

    # dcterms:modified
    mod_el = None
    for m in metadata.findall(".//{*}meta"):
        if m.get("property") == "dcterms:modified":
            mod_el = m
            break
    if mod_el is None:
        mod_el = etree.SubElement(metadata, f"{{{ns_opf}}}meta", property="dcterms:modified")
    mod_el.text = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = root.find(".//{*}manifest")
    spine = root.find(".//{*}spine")
    if manifest is None or spine is None:
        raise RuntimeError("OPF 缺少 manifest 或 spine。")

    # 2. 清洗所有 XHTML 正文中的恶意脚本，规范 text/html -> application/xhtml+xml，并提取章节标题
    id_to_item: dict[str, etree._Element] = {}
    for item in manifest.findall("{*}item"):
        if item.get("id") and item.get("href"):
            id_to_item[item.get("id")] = item

    chapter_links: list[tuple[str, str]] = []
    for ref in spine.findall("{*}itemref"):
        item = id_to_item.get(ref.get("idref") or "")
        if item is None:
            continue
        href = item.get("href") or ""
        if not href:
            continue
        target_file = (opf_dir / href).resolve()
        if not target_file.is_relative_to(work_dir.resolve()) or not target_file.exists():
            continue
        if target_file.suffix.lower() in {".xhtml", ".html", ".htm"}:
            if (item.get("media-type") or "").lower() == "text/html":
                item.set("media-type", "application/xhtml+xml")
            raw_text = target_file.read_text(encoding="utf-8", errors="replace")
            if sanitize_scripts and ("<script" in raw_text.lower() or "<iframe" in raw_text.lower()):
                body_inner = sanitize_xhtml_fragment(raw_text)
                ch_title = html.escape(target_file.stem)
                rebuilt = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    "<!DOCTYPE html>\n"
                    f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{language}">\n'
                    f"<head><title>{ch_title}</title></head>\n"
                    f"<body>{body_inner}</body>\n</html>\n"
                )
                target_file.write_text(rebuilt, encoding="utf-8")
            elif "http://www.w3.org/1999/xhtml" not in raw_text:
                # 将旧版 HTML4/HTML5 转换为合法 XHTML，避免 media-type 改为 application/xhtml+xml 后解析报错
                try:
                    hdoc = lxml_html.fromstring(raw_text)
                    hdoc.tag = "html"
                    hdoc.set("xmlns", "http://www.w3.org/1999/xhtml")
                    xhtml_bytes = etree.tostring(hdoc, encoding="utf-8", method="xml")
                    target_file.write_bytes(
                        b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n' + xhtml_bytes
                    )
                except (etree.Error, ValueError):
                    pass
            # 提取标题用于 nav / ncx
            heading = target_file.stem
            try:
                doc = etree.fromstring(target_file.read_bytes(), etree.HTMLParser())
                for tag in ("h1", "h2", "h3", "title"):
                    h = doc.find(f".//{tag}")
                    if h is not None and "".join(h.itertext()).strip():
                        heading = "".join(h.itertext()).strip()
                        break
            except etree.Error:
                pass
            chapter_links.append((href, heading))

    # 3. 补全封面
    has_cover_prop = any(
        "cover-image" in (it.get("properties") or "").split()
        for it in manifest.findall("{*}item")
    )
    if fix_cover and not has_cover_prop:
        img_dir = opf_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / "refined_cover.svg").write_text(
            generate_svg_cover(title, author), encoding="utf-8"
        )
        etree.SubElement(
            manifest,
            f"{{{ns_opf}}}item",
            id="refined-cover-img",
            href="images/refined_cover.svg",
            **{"media-type": "image/svg+xml", "properties": "cover-image"},
        )

    # 4. 确保存在合法且非空的 nav.xhtml
    nav_item = next(
        (it for it in manifest.findall("{*}item") if "nav" in (it.get("properties") or "").split()),
        None,
    )
    nav_needs_rebuild = nav_item is None
    if nav_item is not None and nav_item.get("href"):
        existing_nav = (opf_dir / nav_item.get("href")).resolve()
        if not existing_nav.is_relative_to(work_dir.resolve()) or not existing_nav.exists():
            nav_needs_rebuild = True
        else:
            try:
                ndoc = etree.parse(str(existing_nav))
                ntitle = ndoc.find(".//{*}title")
                has_title = ntitle is not None and bool("".join(ntitle.itertext()).strip())
                empty_heading = any(
                    not "".join(h.itertext()).strip()
                    for tag in ("h1", "h2", "h3", "h4", "h5", "h6")
                    for h in ndoc.findall(f".//{{*}}{tag}")
                )
                nav_lis = ndoc.findall(".//{*}nav//{*}ol//{*}li")
                if not has_title or empty_heading or len(nav_lis) == 0:
                    nav_needs_rebuild = True
            except etree.Error:
                nav_needs_rebuild = True

    if nav_needs_rebuild:
        effective_links = chapter_links or [("refined_nav.xhtml", html.escape(title or "正文"))]
        lis = "\n".join(
            f'      <li><a href="{html.escape(href)}">{html.escape(label)}</a></li>'
            for href, label in effective_links
        )
        nav_xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">\n'
            f"<head><title>{html.escape(title or '目录')}</title></head>\n"
            '<body>\n  <nav epub:type="toc" id="toc">\n    <h1>目录</h1>\n    <ol>\n'
            f"{lis}\n"
            "    </ol>\n  </nav>\n</body>\n</html>\n"
        )
        if nav_item is not None and nav_item.get("href"):
            nav_target = (opf_dir / nav_item.get("href")).resolve()
            if nav_target.is_relative_to(work_dir.resolve()):
                nav_target.write_text(nav_xhtml, encoding="utf-8")
                nav_item.set("media-type", "application/xhtml+xml")
        else:
            (opf_dir / "refined_nav.xhtml").write_text(nav_xhtml, encoding="utf-8")
            etree.SubElement(
                manifest,
                f"{{{ns_opf}}}item",
                id="refined-nav",
                href="refined_nav.xhtml",
                **{"media-type": "application/xhtml+xml", "properties": "nav"},
            )

    # 4.5 若存在空壳 toc.ncx（navMap 无任何 navPoint），自动用章节列表补全
    ncx_item = next(
        (
            it
            for it in manifest.findall("{*}item")
            if (it.get("media-type") or "").lower() == "application/x-dtbncx+xml"
        ),
        None,
    )
    if ncx_item is not None and ncx_item.get("href") and chapter_links:
        ncx_path = (opf_dir / ncx_item.get("href")).resolve()
        if ncx_path.is_relative_to(work_dir.resolve()) and ncx_path.exists():
            try:
                ncx_tree = etree.parse(str(ncx_path))
                nav_map = ncx_tree.find(".//{*}navMap")
                if nav_map is not None and len(nav_map.findall("{*}navPoint")) == 0:
                    ns_ncx = etree.QName(ncx_tree.getroot()).namespace or "http://www.daisy.org/z3986/2005/ncx/"
                    for idx, (href, label) in enumerate(chapter_links, start=1):
                        np = etree.SubElement(
                            nav_map,
                            f"{{{ns_ncx}}}navPoint",
                            id=f"navPoint-{idx}",
                            playOrder=str(idx),
                        )
                        nl = etree.SubElement(np, f"{{{ns_ncx}}}navLabel")
                        nt = etree.SubElement(nl, f"{{{ns_ncx}}}text")
                        nt.text = label
                        etree.SubElement(np, f"{{{ns_ncx}}}content", src=href)
                    ncx_path.write_bytes(
                        etree.tostring(ncx_tree, xml_declaration=True, encoding="utf-8", pretty_print=True)
                    )
            except etree.Error:
                pass

    if inject_clean_css:
        styles_dir = opf_dir / "styles"
        styles_dir.mkdir(parents=True, exist_ok=True)
        (styles_dir / "refined_main.css").write_text(EBOOK_CSS, encoding="utf-8")
        if not any(it.get("href") == "styles/refined_main.css" for it in manifest.findall("{*}item")):
            etree.SubElement(
                manifest,
                f"{{{ns_opf}}}item",
                id="refined-css",
                href="styles/refined_main.css",
                **{"media-type": "text/css"},
            )

    # 写回前统一规范化 OPF 命名空间为 http://www.idpf.org/2007/opf
    for el in root.iter():
        if isinstance(el.tag, str):
            local = etree.QName(el).localname
            ns = etree.QName(el).namespace
            if ns != ns_dc:
                el.tag = f"{{{ns_opf}}}{local}"
    etree.cleanup_namespaces(root)

    # 写回 OPF
    opf_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="utf-8", pretty_print=True))

    # 重新打包 ZIP（mimetype 必须第一项且 STORED）
    tmp_out = output_path.with_suffix(".epub.tmp")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp_out, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for file_path in sorted(work_dir.rglob("*")):
            if file_path.is_dir():
                continue
            rel = file_path.relative_to(work_dir).as_posix()
            if rel == "mimetype":
                continue
            zf.write(file_path, arcname=rel, compress_type=zipfile.ZIP_DEFLATED)
    tmp_out.replace(output_path)
    return output_path
