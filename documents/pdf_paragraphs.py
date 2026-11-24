"""Reflow PDF and OCR lines into editable Word paragraphs."""

import csv
import re
from functools import lru_cache
from io import StringIO
from itertools import groupby
from statistics import median

ITEM = re.compile(r"^(?:\d+[.)]|\d+(?:\.\d+)+[.)]?|[•▪])\s+")
LEFT_WORD = re.compile(r"(?<![\w-])([А-Яа-яЁё]+)([-\u00ad])$")
RIGHT_WORD = re.compile(r"^([а-яё]+)(?![\w-])")


@lru_cache(maxsize=1)
def dictionary():
    from pymorphy3 import MorphAnalyzer

    return MorphAnalyzer()


def reflow(lines, uncertain):
    paragraphs = []
    previous = None
    for text, left, top, bottom, block in lines:
        if previous:
            old_left, old_bottom, old_block = previous
        height = max(1, bottom - top)
        if (
            previous is None
            or block != old_block
            or ITEM.match(text)
            or top - old_bottom > height * 0.8
            or left - old_left > height
        ):
            paragraphs.append(text)
        else:
            before = paragraphs[-1]
            end = LEFT_WORD.search(before)
            start = RIGHT_WORD.match(text)
            if end and start:
                joined = end[1] + start[1]
                compound = end[1] + "-" + start[1]
                if end[2] == "\u00ad" or (
                    dictionary().word_is_known(joined) and not dictionary().word_is_known(compound)
                ):
                    before = before[:-1]
                elif not dictionary().word_is_known(compound) or dictionary().word_is_known(joined):
                    uncertain.add(compound)
                paragraphs[-1] = before + text
            elif before.endswith(("-", "\u00ad")) and text[0].islower():
                paragraphs[-1] = before.removesuffix("\u00ad") + text
            else:
                paragraphs[-1] += " " + text
        previous = left, bottom, block
    return "\n\n".join(paragraphs)


def ocr_lines(tsv):
    words = (
        row
        for row in csv.DictReader(StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["level"] == "5" and row["text"].strip()
    )
    for key, group in groupby(words, lambda w: (w["block_num"], w["par_num"], w["line_num"])):
        line = list(group)
        yield (
            " ".join(w["text"] for w in line),
            min(int(w["left"]) for w in line),
            min(int(w["top"]) for w in line),
            max(int(w["top"]) + int(w["height"]) for w in line),
            key[:2],
        )


def pdf_lines(words):
    if not words:
        return
    height = median(w["bottom"] - w["top"] for w in words)
    # A continuous vertical gutter separates columns even when their baselines coincide.
    ordered = sorted(words, key=lambda w: w["x0"])
    right = ordered[0]["x1"]
    for index, word in enumerate(ordered[1:], 1):
        if word["x0"] - right > 2.5 * height:
            yield from pdf_lines(ordered[:index])
            yield from pdf_lines(ordered[index:])
            return
        right = max(right, word["x1"])
    rows = []
    for word in sorted(words, key=lambda w: w["top"]):
        if not rows or word["top"] - rows[-1][0]["top"] > max(2, height * 0.35):
            rows.append([])
        rows[-1].append(word)
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        yield (
            " ".join(w["text"] for w in row),
            row[0]["x0"],
            min(w["top"] for w in row),
            max(w["bottom"] for w in row),
            ordered[0]["x0"],
        )
