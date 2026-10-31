"""Load bundled setup instructions without importing a GUI toolkit."""
from html.parser import HTMLParser
from pathlib import Path
import re


class GuideParser(HTMLParser):
    """Convert our bundled guide into readable native text; never render HTML."""
    def __init__(self, origin):
        super().__init__(convert_charrefs=True)
        self.origin = origin
        self.chapters = []
        self.current = None
        self.heading = False
        self.title = []
        self.parts = []
        self.link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and "data-guide-section" in attrs:
            self.current, self.title, self.parts = True, [], []
        if not self.current:
            return
        if tag == "h2":
            self.heading = True
        elif tag in ("p", "h3", "pre", "tr", "div"):
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "a":
            self.link = attrs.get("href")
        if "data-guide-redirect" in attrs:
            self.parts.append(self.origin + "/callback")
        elif "data-guide-local" in attrs:
            self.parts.append(self.origin)

    def handle_endtag(self, tag):
        if not self.current:
            return
        if tag == "h2":
            self.heading = False
        elif tag == "a" and self.link:
            self.parts.append(" (" + self.link + ")")
            self.link = None
        elif tag == "section":
            body = re.sub(r"[ \t]+", " ", "".join(self.parts))
            body = re.sub(r"\n[ \t]+", "\n", body)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            self.chapters.append(("".join(self.title).strip(), body))
            self.current = None

    def handle_data(self, value):
        if self.current:
            (self.title if self.heading else self.parts).append(value)


def load_guide(origin):
    parser = GuideParser(origin)
    parser.feed((Path(__file__).parent / "web/guide.html").read_text(encoding="utf-8"))
    return parser.chapters
