import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

PREFIX = "docstation-rich:v1\n"
TAGS = frozenset("""
a b blockquote br div em font i ins li ol p s span strike strong sub sup table tbody td th thead tr u ul h1 h2 h3 pre code hr cite
""".split())
STYLES = {
    "color": r"#[a-fA-F0-9]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)",
    "background-color": r"#[a-fA-F0-9]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)",
    "text-align": r"left|center|right|justify",
    "margin-left": r"40px|80px|120px|160px",
    "font-size": r"small|medium|large|x-large",
}


class RichText(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.html = []
        self.text = []
        self.stack = []
        self.feed(source)
        self.close()
        self.html.extend(f"</{tag}>" for tag in reversed(self.stack))

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            return
        attrs = dict(attrs)
        allowed = {}
        style = []
        for declaration in (attrs.get("style") or "").split(";"):
            name, _, value = declaration.partition(":")
            name, value = name.strip().lower(), value.strip()
            if name in STYLES and re.fullmatch(STYLES[name], value):
                style.append(f"{name}:{value}")
        if attrs.get("align") in {"left", "center", "right", "justify"}:
            style.append("text-align:" + attrs["align"])
        if style:
            allowed["style"] = ";".join(style)
        if tag == "font":
            if attrs.get("size") in ("2", "3", "4", "5"):
                allowed["size"] = attrs["size"]
            if re.fullmatch(r"#[a-fA-F0-9]{6}", attrs.get("color") or ""):
                allowed["color"] = attrs["color"]
        if tag == "a":
            href = attrs.get("href") or ""
            try:
                scheme = urlsplit(href).scheme
            except ValueError:
                scheme = ""
            if scheme in {"http", "https", "mailto"} and not any(ord(char) < 32 for char in href):
                allowed["href"] = href
                allowed["rel"] = "nofollow noreferrer"
        markup = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in allowed.items())
        self.html.append(f"<{tag}{markup}>")
        if tag not in {"br", "hr"}:
            self.stack.append(tag)
        if tag in {"br", "div", "li", "p", "tr"}:
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack[-1] != tag:
                self.html.append(f"</{self.stack.pop()}>")
            self.html.append(f"</{self.stack.pop()}>")

    def handle_data(self, data):
        self.html.append(escape(data))
        self.text.append(data)


def normalize_newlines(value):
    # Browser multipart forms serialize textarea line breaks as CRLF.
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalize(value):
    value = normalize_newlines(value)
    if not value.startswith(PREFIX):
        return value
    rich = RichText(value.removeprefix(PREFIX))
    return PREFIX + "".join(rich.html) if "".join(rich.text).strip() else ""
