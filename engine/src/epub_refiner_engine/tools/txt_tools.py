"""TXT 电子书编码探测、样本提取与确定性切分清洗工具。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from charset_normalizer import from_bytes

from epub_refiner_engine.models import ChapterPreview, TxtRules

DEFAULT_CHAPTER_REGEX = (
    r"^\s*(?:第[零一二三四五六七八九十百千万两0-9]{1,8}[章节回集幕]\b.*|"
    r"(?:序章|楔子|前言|引子|尾声|后记|番外[^\n]{0,30}))\s*$"
)
DEFAULT_VOLUME_REGEX = r"^\s*(?:第[零一二三四五六七八九十百千万两0-9]{1,5}[卷部辑]\b.*|卷[零一二三四五六七八九十百0-9]{1,4}\s+.*)\s*$"


@dataclass
class ParsedChapter:
    index: int
    volume: str | None
    title: str
    paragraphs: list[str]

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)


_COMMON_ZH_CHARS = frozenset(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严"
)
_MOJIBAKE_SUSPECT_CHARS = frozenset("锟斤拷烫ÃÂÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ")


def _chinese_readability_score(text: str) -> float:
    """评估文本作为正常中文小说的可读性得分（越高越像正常中文）。"""
    sample = text[:4000]
    if not sample:
        return 0.0
    common = sum(1 for ch in sample if ch in _COMMON_ZH_CHARS)
    punct = sum(1 for ch in sample if ch in "，。！？；：“”‘’《》（）、\n ")
    bad = sum(1 for ch in sample if ch == "\ufffd" or ch in _MOJIBAKE_SUSPECT_CHARS)
    return (common * 2.0 + punct * 0.5 - bad * 4.0) / len(sample)


def _try_repair_mojibake(text: str) -> tuple[str, str | None]:
    """检测并逆向修复 UTF-8 被误用 GBK/Latin-1 解码保存导致的乱码汉字。"""
    base_score = _chinese_readability_score(text)
    sample = text[:4000]

    best_score = base_score + 0.15
    best_fixed: str | None = None
    best_enc: str | None = None

    for wrong_enc in ("gbk", "gb18030", "latin-1", "cp1252"):
        # 先试严格无损还原；若存在局部错位字节，按行容错还原（绝不静默丢弃 encode 字符）
        for strict in (True, False):
            try:
                if strict:
                    probe_fixed = sample.encode(wrong_enc).decode("utf-8")
                else:
                    probe_fixed = "\n".join(
                        ln.encode(wrong_enc).decode("utf-8", errors="replace")
                        for ln in sample.splitlines()
                    )
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue

            if len(probe_fixed) < max(10, len(sample) // 4):
                continue

            score = _chinese_readability_score(probe_fixed)
            if score > best_score:
                try:
                    if strict:
                        full_fixed = text.encode(wrong_enc).decode("utf-8")
                    else:
                        full_fixed = "\n".join(
                            ln.encode(wrong_enc).decode("utf-8", errors="replace")
                            for ln in text.splitlines()
                        )
                    best_score = score
                    best_fixed = full_fixed
                    best_enc = wrong_enc
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue

    if best_fixed is not None and best_enc is not None:
        return best_fixed, best_enc
    return text, None


def detect_corruption_warnings(text: str) -> list[str]:
    """检测文本中是否存在不可逆的占位乱码（锟斤拷 / 烫烫烫 / U+FFFD）。"""
    warnings: list[str] = []
    if "\ufffd" in text:
        warnings.append("检测到无法还原的替换符（），原文件在保存前可能已丢失部分字节。")
    if "锟斤拷" in text:
        warnings.append("正文含“锟斤拷”字样（原文件保存时已被不可逆破坏），已尽量保留其余可读内容。")
    if "烫烫烫" in text:
        warnings.append("正文含“烫烫烫”字样，疑似存在内存未初始化脏数据。")
    return warnings


def read_txt_with_encoding(path: str | Path) -> tuple[str, str]:
    """探测 TXT 真实编码（含 UTF-16/GB18030/Big5 及双重乱码修复），返回统一换行符的正常中文。"""
    raw = Path(path).read_bytes()
    if not raw:
        return "", "utf-8"

    # 1. BOM 精确识别
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        repaired, wrong = _try_repair_mojibake(text)
        enc = f"utf-8-sig (已自动还原 {wrong.upper()} 错位乱码)" if wrong else "utf-8-sig"
        return repaired, enc

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        bom_enc = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        text = raw.decode("utf-16").replace("\r\n", "\n").replace("\r", "\n")
        return text, bom_enc

    # 2. 严格 UTF-8（并检查是否为双重编码错位乱码）
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        repaired, wrong = _try_repair_mojibake(text)
        if wrong:
            return repaired, f"utf-8 (已自动还原 {wrong.upper()} 错位乱码)"
        return text, "utf-8"
    except UnicodeDecodeError:
        pass

    # 3. 非 UTF-8 字节流：中文网文优先探测 gb18030 / big5，并统一转为标准 UTF-8 字符串
    best = from_bytes(raw[: min(len(raw), 512_000)]).best()
    encoding = best.encoding if best else "gb18030"
    if encoding.lower() in {"gb2312", "gbk", "cp936"}:
        encoding = "gb18030"

    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        encoding = "gb18030"
        text = raw.decode(encoding, errors="replace")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    repaired, wrong = _try_repair_mojibake(text)
    if wrong:
        return repaired, f"{encoding} (已自动还原 {wrong.upper()} 错位乱码)"
    return text, encoding


def export_clean_utf8_txt(text: str, output_path: Path) -> Path:
    """将修复后的正常中文小说原子写入标准 UTF-8（无 BOM）TXT 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".txt.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(output_path)
    return output_path


def extract_txt_sample(text: str, filename_stem: str) -> str:
    """提取用于给 DeepSeek 推理规则的精简样本（绝不发送整本书）。"""
    lines = text.splitlines()
    total = len(lines)

    head_lines = lines[:180]
    mid_start = max(180, total // 2 - 40)
    mid_lines = lines[mid_start : mid_start + 80] if total > 260 else []

    # 统计高频重复短行（常见广告、盗版网站水印、求订阅提示）
    counter: Counter[str] = Counter()
    for line in lines:
        s = line.strip()
        if 4 <= len(s) <= 80:
            counter[s] += 1
    repeated = [f"{line!r} (出现 {cnt} 次)" for line, cnt in counter.most_common(12) if cnt >= 3]

    parts = [
        f"文件名(不含扩展名): {filename_stem}",
        f"总行数: {total}",
        "--- 开头 180 行样本 ---",
        "\n".join(head_lines),
    ]
    if mid_lines:
        parts.extend(["--- 中段 80 行样本 ---", "\n".join(mid_lines)])
    if repeated:
        parts.extend(["--- 高频重复行候选（疑似广告/水印）---", "\n".join(repeated)])

    return "\n".join(parts)


def split_txt_by_rules(text: str, rules: TxtRules) -> tuple[list[ParsedChapter], list[str]]:
    """按给定正则在本地毫秒级切分章节与清洗广告。"""
    warnings: list[str] = []
    try:
        chapter_re = re.compile(rules.chapter_regex)
    except re.error as exc:
        warnings.append(f"章节正则无效，已回退默认规则 ({exc})")
        chapter_re = re.compile(DEFAULT_CHAPTER_REGEX)

    volume_re: re.Pattern[str] | None = None
    if rules.volume_regex.strip():
        try:
            volume_re = re.compile(rules.volume_regex)
        except re.error as exc:
            warnings.append(f"分卷正则无效，已忽略分卷 ({exc})")

    ad_res: list[re.Pattern[str]] = []
    for pat in rules.ad_regexes:
        if not pat.strip():
            continue
        try:
            ad_res.append(re.compile(pat))
        except re.error as exc:
            warnings.append(f"广告正则无效已跳过: {pat!r} ({exc})")

    chapters: list[ParsedChapter] = []
    current_volume: str | None = None
    current_title = "前言"
    current_paras: list[str] = []

    def flush_chapter() -> None:
        nonlocal current_paras
        if current_paras:
            chapters.append(
                ParsedChapter(
                    index=len(chapters) + 1,
                    volume=current_volume,
                    title=current_title,
                    paragraphs=current_paras,
                )
            )
            current_paras = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 广告过滤
        if any(ad.search(line) for ad in ad_res):
            continue

        if volume_re and volume_re.match(line):
            flush_chapter()
            current_volume = line
            continue

        if chapter_re.match(line):
            flush_chapter()
            current_title = line
            continue

        current_paras.append(line)

    flush_chapter()

    if not chapters:
        # 整本没匹配到任何章节，按固定字数切块兜底
        warnings.append("未能匹配到任何章节标题，已按每 5000 字自动分段。")
        all_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        chunk: list[str] = []
        chunk_len = 0
        for ln in all_lines:
            chunk.append(ln)
            chunk_len += len(ln)
            if chunk_len >= 5000:
                chapters.append(
                    ParsedChapter(
                        index=len(chapters) + 1,
                        volume=None,
                        title=f"第 {len(chapters) + 1} 部分",
                        paragraphs=chunk,
                    )
                )
                chunk = []
                chunk_len = 0
        if chunk:
            chapters.append(
                ParsedChapter(
                    index=len(chapters) + 1,
                    volume=None,
                    title=f"第 {len(chapters) + 1} 部分",
                    paragraphs=chunk,
                )
            )

    # 异常章节体检警告
    short_count = sum(1 for c in chapters if c.char_count < 80)
    long_count = sum(1 for c in chapters if c.char_count > 30_000)
    if short_count:
        warnings.append(f"发现 {short_count} 个极短章节（少于 80 字），请确认是否误把正文当标题。")
    if long_count:
        warnings.append(f"发现 {long_count} 个超长章节（超过 30000 字），可能有章节标题漏匹配。")

    return chapters, warnings


def build_previews(chapters: list[ParsedChapter], limit: int = 8) -> list[ChapterPreview]:
    previews: list[ChapterPreview] = []
    for ch in chapters[:limit]:
        joined = " ".join(ch.paragraphs[:2])
        excerpt = (joined[:90] + "…") if len(joined) > 90 else joined
        previews.append(
            ChapterPreview(
                index=ch.index,
                volume=ch.volume,
                title=ch.title,
                char_count=ch.char_count,
                excerpt=excerpt,
            )
        )
    return previews
