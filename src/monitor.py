#!/usr/bin/env python3
"""Monitor SteamZG category page and send Telegram notifications for new posts."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import hmac
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_TARGET_URL = "https://steamzg.com/category/%E5%8D%95%E6%9C%BA%E6%B8%B8%E6%88%8F/"
DEFAULT_DATA_FILE = "data/seen_games.json"
POST_PATH_RE = re.compile(r"^/\d+/?$")


@dataclass(frozen=True)
class Article:
    title: str
    url: str


class SteamZGParser(HTMLParser):
    """Extract article links from the category page.

    SteamZG category pages expose post links as anchors with paths like /4758/.
    The same post can appear twice: once as an empty thumbnail link and once as
    a title link. Empty links are ignored and duplicates are removed later.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.articles: list[Article] = []
        self._capturing_href: str | None = None
        self._captured_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        attr_map = {name.lower(): value for name, value in attrs}
        href = attr_map.get("href")
        if not href:
            return

        absolute_url = urljoin(self.base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.netloc != "steamzg.com" or not POST_PATH_RE.match(parsed.path):
            return

        self._capturing_href = absolute_url
        self._captured_text = []

    def handle_data(self, data: str) -> None:
        if self._capturing_href is not None:
            self._captured_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._capturing_href is None:
            return

        title = normalize_text("".join(self._captured_text))
        if title:
            self.articles.append(Article(title=title, url=normalize_url(self._capturing_href)))

        self._capturing_href = None
        self._captured_text = []


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if path and not path.endswith("/"):
        path += "/"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def build_page_url(base_url: str, page_number: int) -> str:
    normalized_base = base_url.rstrip("/") + "/"
    if page_number <= 1:
        return normalized_base
    return urljoin(normalized_base, f"page/{page_number}/")
