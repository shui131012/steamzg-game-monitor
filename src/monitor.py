#!/usr/bin/env python3
"""Monitor SteamZG category page and send Telegram notifications for new posts."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
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


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; GitHubActionsMonitor/1.0; "
                "+https://github.com/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    try:
        with urlopen(request, timeout=25) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc


def parse_articles(html: str, base_url: str) -> list[Article]:
    parser = SteamZGParser(base_url)
    parser.feed(html)

    result: list[Article] = []
    seen_urls: set[str] = set()
    for article in parser.articles:
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        result.append(article)

    return result


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        return {"seen": {}}
    if not isinstance(data.get("seen"), dict):
        data["seen"] = {}
    return data


def save_history(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temp_name, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "false",
        }
    ).encode("utf-8")

    request = Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API failed: HTTP {exc.code} {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram API failed: {exc.reason}") from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Telegram API returned invalid JSON: {body}") from exc

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API returned an error: {body}")


def build_message(article: Article) -> str:
    return f"发现新的单机游戏文章：\n\n{article.title}\n{article.url}"


def trim_history(seen: dict[str, Any], max_items: int) -> dict[str, Any]:
    if max_items <= 0 or len(seen) <= max_items:
        return seen

    sorted_items = sorted(
        seen.items(),
        key=lambda item: item[1].get("first_seen_at", "") if isinstance(item[1], dict) else "",
        reverse=True,
    )
    return dict(sorted_items[:max_items])


def main() -> int:
    target_url = os.getenv("TARGET_URL", DEFAULT_TARGET_URL)
    data_file = Path(os.getenv("DATA_FILE", DEFAULT_DATA_FILE))
    first_run_notify = env_bool("FIRST_RUN_NOTIFY", False)
    max_seen_items = int(os.getenv("MAX_SEEN_ITEMS", "500"))
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    html = fetch_html(target_url)
    articles = parse_articles(html, target_url)
    if not articles:
        raise RuntimeError("No articles found. The website layout may have changed.")

    history = load_history(data_file)
    seen: dict[str, Any] = history["seen"]
    is_first_run = len(seen) == 0
    new_articles = [article for article in articles if article.url not in seen]

    print(f"Found {len(articles)} articles on page.")
    print(f"Found {len(new_articles)} new articles.")

    timestamp = now_iso()

    if is_first_run and not first_run_notify:
        print("First run detected. Saving current articles without sending notifications.")
    elif new_articles:
        if not telegram_token or not telegram_chat_id:
            raise RuntimeError(
                "New articles were found, but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
            )

        for article in reversed(new_articles):
            print(f"Sending Telegram notification: {article.title}")
            send_telegram_message(telegram_token, telegram_chat_id, build_message(article))

    for article in articles:
        if article.url not in seen:
            seen[article.url] = {
                "title": article.title,
                "first_seen_at": timestamp,
            }
        else:
            if isinstance(seen[article.url], dict):
                seen[article.url]["title"] = article.title

    history["seen"] = trim_history(seen, max_seen_items)
    history["last_checked_at"] = timestamp
    history["target_url"] = target_url
    save_history(data_file, history)

    print(f"History saved to {data_file}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
