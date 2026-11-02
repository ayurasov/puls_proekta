"""Текст из файла MS Project: заметки без форматирования, сравнение ФИО.

MS Project пишет заметки в XML простым текстом, но файлы из .mpp через конвертер, Project Server и сторонние
планировщики иногда кладут туда RTF (`{\\rtf1…}`) или HTML. В «Пульс» заметка попадает комментарием, поэтому
форматирование снимается: остаются абзацы и текст (ADR-0012, раздел 3: «RTF не переносится»).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# Группы RTF, содержимое которых не текст: таблицы шрифтов и цветов, стили, сведения, картинки
_RTF_SKIP = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "headerl", "headerr",
    "footerl", "footerr", "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "themedata",
    "colorschememapping", "latentstyles", "datastore", "filetbl", "revtbl", "mmathPr", "pgdsctbl",
}
_RTF_SPECIAL = {"par": "\n", "line": "\n", "sect": "\n", "page": "\n", "tab": "\t", "cell": " ", "row": "\n",
                "emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’", "ldblquote": "“",
                "rdblquote": "”", "emspace": " ", "enspace": " ", "qmspace": " "}
# Управляющее слово с числом, \'hh, управляющий символ, скобка группы, текст, перевод строки (не текст в RTF)
_RTF_TOKEN = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])"
                        r"|([^\\{}\r\n]+)|[\r\n]+")
_HTML_HINT = re.compile(r"<\s*(html|body|p|div|br|span|font|b|i|u|ul|ol|li|table)\b", re.IGNORECASE)
_HTML_BREAK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
# NUL и управляющие символы (кроме табуляции и перевода строки), одиночные суррогаты, не-символы U+FFFE/U+FFFF:
# из RTF (\'00, \u0, \u55296) и HTML они попали бы в комментарий, а PostgreSQL не принимает NUL в тексте
_UNSAFE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")


def clean(value):
    """Текст без символов, которые нельзя сохранить в базе или записать в XML; None — пустая строка."""
    return _UNSAFE.sub("", str(value)) if value is not None else ""


def plain_text(value):
    """Заметка без RTF/HTML, с нормализованными переводами строк; пустая — None."""
    if value is None:
        return None
    text = str(value)
    stripped = text.lstrip()
    if stripped.startswith("{\\rtf"):
        text = rtf_to_text(stripped)
    elif _HTML_HINT.search(text):
        text = html_to_text(text)
    text = clean(text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " "))
    lines = [line.rstrip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or None


def rtf_to_text(rtf):
    """Текст документа RTF: управляющие слова отбрасываются, \\par — абзац, \\'hh — символ кодовой страницы
    (\\ansicpg, по умолчанию 1251), \\uN — символ Юникода с пропуском \\ucN замещающих символов."""
    out = []
    stack = []
    skip = False          # внутри группы, которую не выводим
    codepage = "cp1251"
    uc_skip, pending_skip = 1, 0
    hex_bytes = bytearray()

    def flush_hex():
        if hex_bytes:
            out.append(bytes(hex_bytes).decode(codepage, errors="replace"))
            hex_bytes.clear()

    first_in_group = False
    for match in _RTF_TOKEN.finditer(rtf):
        word, arg, hex_code, symbol, brace, chars = match.groups()
        if hex_code is not None:
            if pending_skip:
                pending_skip -= 1
                continue
            if not skip:
                hex_bytes.append(int(hex_code, 16))
            continue
        flush_hex()
        if brace == "{":
            stack.append((skip, uc_skip))
            first_in_group = True
            continue
        if brace == "}":
            skip, uc_skip = stack.pop() if stack else (False, 1)
            first_in_group = False
            continue
        if symbol is not None:
            first_in_group = False
            if symbol == "*":
                skip = True       # {\*\destination …} — необязательная группа, не текст
            elif pending_skip:
                pending_skip -= 1
            elif not skip and symbol in "\\{}":
                out.append(symbol)
            elif not skip and symbol == "~":
                out.append(" ")
            elif not skip and symbol in "-_":
                out.append("-" if symbol == "_" else "")
            continue
        if word is not None:
            if first_in_group and word in _RTF_SKIP:
                skip = True
            first_in_group = False
            if word == "ansicpg" and arg:
                codepage = f"cp{arg}"
                try:
                    "".encode(codepage)
                except LookupError:
                    codepage = "cp1251"
            elif word == "uc" and arg is not None:
                uc_skip = max(0, min(int(arg), 16))
            elif word == "u" and arg is not None:
                code = int(arg)
                code = code + 65536 if code < 0 else code
                # Вне Юникода (\u1114112) и суррогаты — не символ: пропускаются, а не роняют разбор
                if not skip and 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
                    out.append(chr(code))
                pending_skip = uc_skip
            elif not skip and word in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[word])
            continue
        if chars is not None:
            first_in_group = False
            if pending_skip:
                skipped = min(pending_skip, len(chars))
                chars = chars[skipped:]
                pending_skip -= skipped
            if not skip and chars:
                out.append(chars)
    flush_hex()
    return "".join(out)


class _HtmlText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.hidden += 1
        elif tag in _HTML_BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in _HTML_BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(re.sub(r"[ \t\r\n]+", " ", data))


def html_to_text(value):
    parser = _HtmlText()
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # noqa: BLE001 — разбор «как получится»: заметка не должна ломать импорт
        return html.unescape(re.sub(r"<[^>]*>", " ", value))
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    # Конец абзаца и начало следующего — один перевод строки, а не пустая строка между абзацами
    return re.sub(r"\n{2,}", "\n", "\n".join(lines))


def norm_name(value):
    """ФИО для сравнения: без регистра, ё = е, без лишних пробелов."""
    return " ".join(str(value or "").replace("ё", "е").replace("Ё", "Е").lower().split())


def norm_email(value):
    return str(value or "").strip().lower()
