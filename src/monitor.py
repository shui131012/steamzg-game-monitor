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


def collect_articles_until_known(
    target_url: str,
    latest_seen_url: str,
    max_pages: int,
) -> tuple[list[Article], list[Article], int, bool]:
    collected_new_candidates: list[Article] = []
    scanned_articles: list[Article] = []
    known_urls: set[str] = set()
    candidate_urls: set[str] = set()
    reached_known_boundary = False
    pages_scanned = 0

    for page_number in range(1, max_pages + 1):
        page_url = build_page_url(target_url, page_number)
        page_articles = parse_articles(fetch_html(page_url), page_url)
        pages_scanned += 1

        if not page_articles:
            break

        for article in page_articles:
            if article.url not in known_urls:
                known_urls.add(article.url)
                scanned_articles.append(article)

            if latest_seen_url and article.url == latest_seen_url:
                reached_known_boundary = True
                break

            if article.url not in candidate_urls:
                candidate_urls.add(article.url)
                collected_new_candidates.append(article)

        if reached_known_boundary:
            break

    return collected_new_candidates, scanned_articles, pages_scanned, reached_known_boundary


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        return {"seen": {}}
    if not isinstance(data.get("seen"), dict):
        data["seen"] = {}
    if not isinstance(data.get("latest_seen_url"), str):
        data["latest_seen_url"] = ""
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


def build_bookmark_button_url(article: Article) -> str | None:
    worker_url = os.getenv("BOOKMARK_WORKER_URL", "").strip()
    signing_secret = os.getenv("BOOKMARK_SIGNING_SECRET", "").strip()
    if not worker_url or not signing_secret:
        return None

    payload = f"{article.title}\n{article.url}".encode("utf-8")
    signature = hmac.new(signing_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    query = urlencode({"title": article.title, "url": article.url, "sig": signature})
    separator = "&" if "?" in worker_url else "?"
    return f"{worker_url}{separator}{query}"


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    bookmark_url: str | None = None,
) -> None:
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "false",
    }
    if bookmark_url:
        data["reply_markup"] = json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "保存到游戏书签", "url": bookmark_url},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    payload = urlencode(data).encode("utf-8")

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


def build_summary_message(new_count: int, pages_scanned: int) -> str:
    return (
        f"距离上一次监控，共发现 {new_count} 个新增游戏。\n"
        f"本次共检查了 {pages_scanned} 页，接下来会逐条发送。"
    )


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
    max_seen_items = int(os.getenv("MAX_SEEN_ITEMS", "2000"))
    max_pages_to_scan = int(os.getenv("MAX_PAGES_TO_SCAN", "20"))
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    history = load_history(data_file)
    seen: dict[str, Any] = history["seen"]
    latest_seen_url = normalize_url(history.get("latest_seen_url", "")) if history.get("latest_seen_url") else ""
    is_first_run = len(seen) == 0

    front_page_url = build_page_url(target_url, 1)
    front_page_articles = parse_articles(fetch_html(front_page_url), front_page_url)
    if not front_page_articles:
        raise RuntimeError("No articles found. The website layout may have changed.")

    new_candidates, scanned_articles, pages_scanned, reached_known_boundary = collect_articles_until_known(
        target_url=target_url,
        latest_seen_url=latest_seen_url,
        max_pages=max_pages_to_scan,
    )

    new_articles = [article for article in new_candidates if article.url not in seen]

    print(f"Scanned {pages_scanned} page(s).")
    print(f"Found {len(scanned_articles)} unique articles across scanned pages.")
    print(f"Found {len(new_articles)} new articles.")
    if latest_seen_url and not reached_known_boundary:
        print(
            "Warning: did not reach the previous latest article within the scan limit. "
            "Consider increasing MAX_PAGES_TO_SCAN if updates are extremely heavy."
        )

    timestamp = now_iso()

    if is_first_run and not first_run_notify:
        print("First run detected. Saving current articles without sending notifications.")
    elif new_articles:
        if not telegram_token or not telegram_chat_id:
            raise RuntimeError(
                "New articles were found, but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
            )

        send_telegram_message(
            telegram_token,
            telegram_chat_id,
            build_summary_message(len(new_articles), pages_scanned),
        )
        for article in reversed(new_articles):
            print(f"Sending Telegram notification: {article.title}")
            send_telegram_message(
                telegram_token,
                telegram_chat_id,
                build_message(article),
                bookmark_url=build_bookmark_button_url(article),
            )

    for article in scanned_articles:
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
    history["latest_seen_url"] = front_page_articles[0].url
    history["last_scan_pages"] = pages_scanned
    history["last_new_count"] = len(new_articles)
    save_history(data_file, history)

    print(f"History saved to {data_file}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
