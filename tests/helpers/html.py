"""Semantic HTML assertions for route tests.

Route tests historically asserted on raw substrings of ``response.text``.
That couples them to markup details: a whitespace change, an added wrapper
element, or an attribute reorder breaks a test that was never about markup.

``HTMLDocument`` wraps BeautifulSoup and exposes only the queries tests
actually need -- select by CSS selector, read text, read attributes, count
matches -- so assertions describe *semantics* ("the page has one element with
id ``issue-title`` and its text is X") rather than byte sequences.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from bs4.element import Tag

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Collapse runs of whitespace so template indentation is irrelevant."""
    return _WHITESPACE.sub(" ", value).strip()


class HTMLDocument:
    """A parsed HTML response body with semantic query helpers."""

    def __init__(self, markup: str) -> None:
        self.markup = markup
        self.soup = BeautifulSoup(markup, "html.parser")

    @classmethod
    def from_response(cls, response: Any) -> HTMLDocument:
        """Build a document from an httpx/TestClient response."""
        return cls(response.text)

    # -- selection ---------------------------------------------------------

    def select(self, selector: str) -> list[Tag]:
        """Return every element matching ``selector``."""
        return list(self.soup.select(selector))

    def select_one(self, selector: str) -> Tag | None:
        """Return the first element matching ``selector``, or ``None``."""
        return self.soup.select_one(selector)

    def require(self, selector: str) -> Tag:
        """Return the first match for ``selector``, failing if there is none."""
        element = self.soup.select_one(selector)
        if element is None:
            raise AssertionError(f"no element matched selector {selector!r}")
        return element

    def count(self, selector: str) -> int:
        """Return how many elements match ``selector``."""
        return len(self.soup.select(selector))

    def exists(self, selector: str) -> bool:
        """Return whether at least one element matches ``selector``."""
        return self.soup.select_one(selector) is not None

    # -- text --------------------------------------------------------------

    def text(self, selector: str | None = None) -> str:
        """Return normalized text of the first match (or the whole document)."""
        node: Any = self.soup if selector is None else self.require(selector)
        return normalize_text(node.get_text(" "))

    def texts(self, selector: str) -> list[str]:
        """Return normalized text for every element matching ``selector``."""
        return [normalize_text(el.get_text(" ")) for el in self.soup.select(selector)]

    def has_text(self, needle: str, selector: str | None = None) -> bool:
        """Return whether ``needle`` appears in the normalized text."""
        return normalize_text(needle) in self.text(selector)

    # -- attributes --------------------------------------------------------

    def attr(self, selector: str, name: str) -> str | None:
        """Return attribute ``name`` of the first element matching ``selector``."""
        value = self.require(selector).get(name)
        if isinstance(value, list):
            return " ".join(value)
        return value

    def attrs(self, selector: str, name: str) -> list[str]:
        """Return attribute ``name`` for every element matching ``selector``."""
        values: list[str] = []
        for el in self.soup.select(selector):
            value = el.get(name)
            if value is None:
                continue
            values.append(" ".join(value) if isinstance(value, list) else str(value))
        return values

    def links(self) -> list[str]:
        """Return every ``href`` on the page."""
        return self.attrs("a[href]", "href")

    # -- scoping -----------------------------------------------------------

    def scope(self, selector: str) -> HTMLDocument:
        """Return a document limited to the first element matching ``selector``."""
        return HTMLDocument(str(self.require(selector)))

    def section_by_heading(
        self,
        heading: str,
        *,
        container: str = "section",
        heading_selector: str = "h1, h2, h3, h4, h5, h6",
    ) -> HTMLDocument:
        """Return the container whose first heading reads ``heading``.

        Templates that repeat an unlabelled wrapper (``<section class="card">``)
        give tests nothing stable to select on. Addressing such a block by its
        visible heading is still semantic, and survives markup churn inside it.
        """
        wanted = normalize_text(heading)
        for container_el in self.soup.select(container):
            heading_el = container_el.select_one(heading_selector)
            if (
                heading_el is not None
                and normalize_text(heading_el.get_text(" ")) == wanted
            ):
                return HTMLDocument(str(container_el))
        raise AssertionError(f"no {container!r} found with heading {heading!r}")

    def has_section(self, heading: str, *, container: str = "section") -> bool:
        """Return whether a container with the given heading exists."""
        try:
            self.section_by_heading(heading, container=container)
        except AssertionError:
            return False
        return True


def parse_html(source: Any) -> HTMLDocument:
    """Parse a response object or raw markup string into an `HTMLDocument`."""
    if isinstance(source, str):
        return HTMLDocument(source)
    return HTMLDocument.from_response(source)
