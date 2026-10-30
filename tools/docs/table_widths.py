"""Ширины столбцов таблиц Markdown для pandoc: число дефисов в строке-разделителе пропорционально
средней длине текста в столбце (pandoc задаёт по нему относительную ширину столбцов в DOCX и PDF).

    python3 tools/docs/table_widths.py docs/manuals/*.md
"""
import re
import sys
from pathlib import Path

SEPARATOR = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+\s*$")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def fix(text):
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if i > 0 and SEPARATOR.match(lines[i]) and lines[i - 1].startswith("|"):
            header = i - 1
            end = i + 1
            while end < len(lines) and lines[end].startswith("|"):
                end += 1
            rows = [cells(lines[header])] + [cells(line) for line in lines[i + 1:end]]
            cols = len(rows[0])
            avg = [sum(len(r[c]) if c < len(r) else 0 for r in rows) / len(rows) for c in range(cols)]
            total = sum(avg) or 1
            seps = cells(lines[i])
            out = []
            # не уже самого длинного слова столбца — иначе слова переносятся по буквам
            longest = [max((len(w) for r in rows if c < len(r) for w in r[c].split()), default=3) for c in range(cols)]
            for c in range(cols):
                width = max(6, round(72 * avg[c] / total), min(30, longest[c] + 2))
                sep = seps[c] if c < len(seps) else "---"
                left, right = sep.startswith(":"), sep.endswith(":") and len(sep) > 1
                out.append((":" if left else "") + "-" * width + (":" if right else ""))
            lines[i] = "| " + " | ".join(out) + " |"
            i = end
        i += 1
    return "\n".join(lines)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        path = Path(name)
        path.write_text(fix(path.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"  {name}")
