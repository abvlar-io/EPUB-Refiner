from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from epub_refiner_engine.server import app
from epub_refiner_engine.tools.epub_builder import safe_extract_zip

client = TestClient(app)


def test_reject_unsupported_extension(tmp_path: Path) -> None:
    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    r = client.post("/api/tasks/diagnose", json={"file_path": str(pdf_file)})
    assert r.status_code == 400
    assert ".txt 和 .epub" in r.json()["detail"]


def test_zip_slip_blocked(tmp_path: Path) -> None:
    evil_zip = tmp_path / "evil.epub"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../outside.txt", "boom")
    dest = tmp_path / "extract"
    dest.mkdir()
    try:
        safe_extract_zip(evil_zip, dest)
        raise AssertionError("应该拦截 Zip Slip 路径穿越")
    except ValueError as exc:
        assert "Zip Slip" in str(exc)


def test_txt_gb18030_to_epub3_passes_epubcheck(tmp_path: Path) -> None:
    novel_lines = [
        "书名：星海漫游指南",
        "作者：银河旅人",
        "第一卷 初入星海",
        "第1章 启程之日",
        "清晨的阳光洒在太空港的金属甲板上，飞船引擎发出低沉的轰鸣。",
        "www.biquge-fake.com 最新章节第一时间更新！",
        "第2章 跃迁星门",
        "穿过蓝色光环的瞬间，整艘护卫舰仿佛被拉长成了一道流光。",
        "www.biquge-fake.com 最新章节第一时间更新！",
        "第3章 未知信号",
        "雷达屏幕上忽然闪烁起一段极其古老的摩斯电码，令舰长陷入沉思。",
        "www.biquge-fake.com 最新章节第一时间更新！",
    ]
    txt_file = tmp_path / "星海漫游指南.txt"
    txt_file.write_bytes("\r\n".join(novel_lines).encode("gb18030"))

    # 1. 诊断阶段
    r1 = client.post("/api/tasks/diagnose", json={"file_path": str(txt_file)})
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["state"] == "awaiting_confirm"
    report = data1["txt_report"]
    assert report["detected_encoding"] == "gb18030"
    assert report["chapter_count"] >= 3

    # 2. 人工确认生成阶段
    task_id = data1["task_id"]
    r2 = client.post(f"/api/tasks/{task_id}/confirm-txt", json=report["rules"])
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["state"] == "completed"
    assert data2["final_check"]["passed"] is True
    assert data2["final_check"]["fatal_count"] == 0
    assert data2["final_check"]["error_count"] == 0

    out_epub = Path(data2["output_path"])
    assert out_epub.exists()
    assert txt_file.exists()  # 原文件未被修改或删除


def test_rough_epub_refine_and_sanitize_passes_epubcheck(tmp_path: Path) -> None:
    rough_epub = tmp_path / "粗糙样书.epub"
    with zipfile.ZipFile(rough_epub, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/opf" version="2.0" unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="id">urn:uuid:11111111-1111-1111-1111-111111111111</dc:identifier>'
            "<dc:language>zh-CN</dc:language>"
            "</metadata>"
            "<manifest>"
            '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest>"
            "<spine><itemref idref=\"ch1\"/></spine>"
            "</package>",
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            "<head><title>第一章</title></head>"
            '<body><h1>第一章 归来</h1><script>alert("xss")</script><p>正文内容。</p></body>'
            "</html>",
        )

    # 1. 体检
    r1 = client.post("/api/tasks/diagnose", json={"file_path": str(rough_epub)})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["state"] == "awaiting_confirm"
    codes = {i["code"] for i in d1["epub_report"]["issues"]}
    assert "MISSING_COVER" in codes
    assert "MISSING_TOC" in codes

    # 2. 确认精排修复
    task_id = d1["task_id"]
    r2 = client.post(
        f"/api/tasks/{task_id}/confirm-epub",
        json={
            "title": "精排修复测试书",
            "author": "测试作者",
            "language": "zh-CN",
            "fix_cover": True,
            "sanitize_scripts": True,
        },
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["state"] == "completed"
    assert d2["final_check"]["passed"] is True, d2["final_check"]["messages"]

    # 3. 确认危险 script 已被清除
    out_epub = Path(d2["output_path"])
    with zipfile.ZipFile(out_epub, "r") as zf:
        ch1_text = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
        assert "<script" not in ch1_text.lower()


def test_mojibake_txt_auto_repair_and_export_utf8(tmp_path: Path) -> None:
    normal_novel = (
        "书名：天际漫步测试\n"
        "作者：夜航旅人\n"
        "第1章 初雪降临\n"
        "北风卷着雪花落在了青石板路上，街角茶馆灯笼微微摇晃，掌柜低头算账\n"
        "第2章 归客到来\n"
        "门帘被一把掀开，走进来一位披着玄色斗篷的年轻人，眉眼间带着风尘"
    )
    # 模拟常见“双重编码乱码”：把正常 UTF-8 字节误用 Latin-1 解码后再以 UTF-8 保存
    mojibake_text = normal_novel.encode("utf-8").decode("latin-1")
    assert "第1章" not in mojibake_text

    garbled_file = tmp_path / "乱码小说.txt"
    garbled_file.write_text(mojibake_text, encoding="utf-8")

    r1 = client.post("/api/tasks/diagnose", json={"file_path": str(garbled_file)})
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    rep = d1["txt_report"]
    assert rep["mojibake_repaired"] is True
    assert "第1章 初雪降临" in rep["sample_text_preview"]
    assert rep["chapter_count"] >= 2

    # 导出纯净 UTF-8 TXT
    r_txt = client.post(f"/api/tasks/{d1['task_id']}/export-utf8-txt", json={"title": "天际漫步"})
    assert r_txt.status_code == 200, r_txt.text
    out_txt = Path(r_txt.json()["utf8_txt_output_path"])
    assert out_txt.exists()
    repaired_content = out_txt.read_text(encoding="utf-8")
    assert "北风卷着雪花落在了青石板路上" in repaired_content


def test_utf16_txt_to_clean_utf8(tmp_path: Path) -> None:
    content = "第1章 黎明\n清晨的第一缕阳光照进了书房。\n第2章 远行\n行囊已经收拾妥当。"
    utf16_file = tmp_path / "utf16小说.txt"
    utf16_file.write_bytes(content.encode("utf-16"))

    r1 = client.post("/api/tasks/diagnose", json={"file_path": str(utf16_file)})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["txt_report"]["detected_encoding"].startswith("utf-16")

    r_txt = client.post(f"/api/tasks/{d1['task_id']}/export-utf8-txt", json={"title": "黎明远行"})
    assert r_txt.status_code == 200
    out_txt = Path(r_txt.json()["utf8_txt_output_path"])
    assert out_txt.read_text(encoding="utf-8") == content


def test_empty_nav_ncx_and_text_html_epub_repaired_locally(tmp_path: Path) -> None:
    """复现并验证：空壳 nav.xhtml、空壳 toc.ncx 与 text/html spine 条目在本地即可修到 0 报错。"""
    src_epub = tmp_path / "broken_toc.epub"
    with zipfile.ZipFile(src_epub, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>荷马颂诗测试</dc:title>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="ch1" href="ch1.html" media-type="text/html"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
  </spine>
</package>""",
        )
        zf.writestr(
            "EPUB/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title></title></head><body><nav epub:type="toc"><h1></h1><ol></ol></nav></body></html>',
        )
        zf.writestr(
            "EPUB/toc.ncx",
            '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>荷马颂诗测试</text></docTitle><navMap></navMap></ncx>',
        )
        zf.writestr(
            "EPUB/ch1.html",
            "<html><head><title>第一首 致阿波罗</title></head><body><h1>第一首 致阿波罗</h1><p>正文内容。</p></body></html>",
        )

    r1 = client.post("/api/tasks/diagnose", json={"file_path": str(src_epub), "mode": "epub_refine"})
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["epub_report"]["initial_check"]["passed"] is False

    r2 = client.post(
        f"/api/tasks/{d1['task_id']}/confirm-epub",
        json={
            "title": "荷马颂诗测试",
            "author": "佚名",
            "language": "zh-CN",
            "fix_cover": True,
            "sanitize_scripts": True,
        },
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["state"] == "completed"
    assert d2["final_check"]["passed"] is True, d2["final_check"]["messages"]


