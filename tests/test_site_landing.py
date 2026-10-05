"""Contract checks for the static NoAutonomo.ai landing in ``site/``.

The landing is published outside the application. These checks pin the
parts of ``site/DESIGN.md`` that a reviewer cannot see in a screenshot:
no scripts or inline styles (the page ships with a strict CSP), the shared
design tokens equal the application's, and the required honest wording
is present while forbidden wording is absent.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
APP_CSS = ROOT / "frontend" / "src" / "styles" / "styles.css"

REQUIRED_PHRASES = (
    "не отправляет декларацию в AEAT",
    "Установленная версия может отличаться от репозитория",
    "расходы пока не измерены",
    "Это не налоговая консультация",
    "Условия использования кода оформляются",
    "Вымышленный пример",
)
FORBIDDEN_PHRASES = ("open source", "open-source", "opensource")
CHAT_URL = "https://t.me/noautonomoai"


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag in {"style", "script"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


def _page() -> _Page:
    page = _Page()
    page.feed((SITE / "index.html").read_text(encoding="utf-8"))
    return page


def _root_tokens(css: str) -> dict[str, str]:
    match = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert match, "no :root block"
    return dict(re.findall(r"(--[a-z-]+)\s*:\s*([^;]+);", match.group(1)))


def test_landing_has_no_scripts_inline_styles_or_handlers() -> None:
    page = _page()
    assert not [tag for tag, _ in page.tags if tag == "script"]
    assert not [tag for tag, _ in page.tags if tag == "style"]
    for tag, attrs in page.tags:
        assert "style" not in attrs, tag
        assert not [name for name in attrs if name.startswith("on")], tag
    csp = [a.get("content") for t, a in page.tags if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"]
    assert csp and "default-src 'none'" in csp[0] and "style-src 'self'" in csp[0]
    stylesheets = [a.get("href") for t, a in page.tags if t == "link" and a.get("rel") == "stylesheet"]
    assert stylesheets == ["landing.css"]


def test_landing_document_structure() -> None:
    page = _page()
    html = [a for t, a in page.tags if t == "html"]
    assert html and html[0].get("lang") == "ru"
    assert sum(1 for t, _ in page.tags if t == "h1") == 1
    assert [t for t, _ in page.tags if t in {"header", "main", "footer"}] == ["header", "main", "footer"]
    hrefs = [a.get("href") or "" for t, a in page.tags if t == "a"]
    assert hrefs and all(h.startswith(("#", "/", "https://")) for h in hrefs), hrefs
    assert not [h for h in hrefs if "/demo" in h], "demo link must not render before the demo exists"
    assert hrefs.count(CHAT_URL) >= 2, "primary call to action repeats at the end"
    assert not [a for t, a in page.tags if t == "a" and "target" in a]
    assert not [t for t, _ in page.tags if t in {"form", "input", "img", "iframe"}]


def test_landing_wording_is_honest() -> None:
    text = " ".join(" ".join(_page().text).split())
    for phrase in REQUIRED_PHRASES:
        assert phrase in text, phrase
    lowered = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in lowered, phrase
    assert "ё" not in text


def test_landing_tokens_match_the_application() -> None:
    app = _root_tokens(APP_CSS.read_text(encoding="utf-8"))
    site = _root_tokens((SITE / "landing.css").read_text(encoding="utf-8"))
    shared = {name: value for name, value in site.items() if name in app}
    assert len(shared) >= 10
    drift = {name: (value, app[name]) for name, value in shared.items() if value.strip() != app[name].strip()}
    assert not drift, drift
