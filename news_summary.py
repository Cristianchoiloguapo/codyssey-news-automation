"""Codyssey Project B - RSS -> topic filter -> AI summary -> Notion.

Usage:
    python news_summary.py --dry-run    # RSS/content only; API keys not needed
    python news_summary.py --check      # Verify required configuration
    python news_summary.py --run        # One article -> AI -> Notion
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

LOG = logging.getLogger("codyssey.news")
NOTION_VERSION = "2025-09-03"
NOTION_ROOT = "https://api.notion.com/v1"
USER_AGENT = "CodysseyNewsProject/1.0 (educational RSS news summarizer)"
DEFAULT_FEED = "https://techcrunch.com/category/artificial-intelligence/feed/"
DEFAULT_OPENAI_BASE_URL = "https://copa.codyssey.kr/v1"
DEFAULT_KEYWORDS = "AI,OpenAI,ChatGPT,LLM,artificial intelligence,generative AI,machine learning"


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    published_at: str
    short_description: str


class PermanentConfigError(RuntimeError):
    """Non-retriable API configuration or permission failure."""


def clean_url(url: str) -> str:
    """Keep canonical URL syntax, remove fragment (safe, deterministic dedupe)."""
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("Invalid article link")
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or "/", u.query, ""))


def keyword_match(value: str, keywords: list[str]) -> bool:
    haystack = value.casefold()
    for term in keywords:
        term = term.strip().casefold()
        if not term:
            continue
        # "ai" should not match common words such as "said".
        if term == "ai":
            if re.search(r"(?<![a-z])ai(?![a-z])", haystack):
                return True
        elif term in haystack:
            return True
    return False


def parse_date(entry: Any) -> str:
    for field in ("published_parsed", "updated_parsed"):
        value = entry.get(field)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass
    for field in ("published", "updated"):
        value = entry.get(field)
        if value:
            try:
                d = parsedate_to_datetime(value)
                return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()
            except (TypeError, ValueError, IndexError):
                pass
    # Do not fabricate publication dates when source lacks them.
    return ""


def fetch_entries(feed_urls: list[str], keywords: list[str]) -> list[Article]:
    import feedparser

    output: dict[str, Article] = {}
    for feed_url in feed_urls:
        LOG.info("RSS 읽는 중: %s", feed_url)
        try:
            resp = requests.get(feed_url, timeout=20, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                raise RuntimeError(f"Cannot parse RSS: {feed.bozo_exception}")
        except (requests.RequestException, RuntimeError) as exc:
            LOG.warning("RSS 수집 실패 (%s): %s", feed_url, exc)
            continue
        for entry in feed.entries:
            title = BeautifulSoup(str(entry.get("title", "")), "html.parser").get_text(" ", strip=True)
            short = BeautifulSoup(str(entry.get("summary", "")), "html.parser").get_text(" ", strip=True)
            if not keyword_match(f"{title} {short}", keywords):
                continue
            try:
                url = clean_url(str(entry.get("link", "")))
            except ValueError:
                continue
            published_at = parse_date(entry)
            if not title or not published_at:
                LOG.info("제목 또는 발행일시 누락으로 제외: %s", url)
                continue
            output[url] = Article(title=title, url=url, published_at=published_at, short_description=short)
    return sorted(output.values(), key=lambda x: x.published_at, reverse=True)


def extract_article_text(article: Article) -> str:
    """Fetch original HTML; never invent or summarize an empty article."""
    import trafilatura

    res = requests.get(article.url, timeout=25, headers={"User-Agent": USER_AGENT})
    res.raise_for_status()
    text = trafilatura.extract(res.text, include_comments=False, include_tables=False) or ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 250:
        raise ValueError("본문이 너무 짧거나 추출 실패 (250자 미만)")
    return text[:12000]


def retry_call(fn, label: str, max_retries: int = 2):
    """One initial attempt + at most 2 retries."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except PermanentConfigError:
            # 400/401/403 etc. cannot be fixed by waiting: fail fast.
            raise
        except Exception as exc:  # transient network/API errors get bounded retries
            if attempt >= max_retries:
                raise RuntimeError(f"{label}: {max_retries + 1}회 시도 후 실패") from exc
            LOG.warning("%s 오류, 재시도 %d/%d: %s", label, attempt + 1, max_retries, exc)
            time.sleep(2 ** attempt)


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_post(path: str, token: str, payload: dict) -> dict:
    resp = requests.post(f"{NOTION_ROOT}{path}", headers=notion_headers(token), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Notion HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def already_saved(data_source_id: str, token: str, url: str) -> bool:
    result = retry_call(
        lambda: notion_post(
            f"/data_sources/{data_source_id}/query",
            token,
            {"filter": {"property": "원문 링크", "url": {"equals": url}}, "page_size": 1},
        ),
        "Notion 중복 검사",
    )
    return bool(result.get("results"))


def summarize_article(title: str, body: str, api_key: str, model: str, base_url: str) -> str:
    from openai import APIStatusError, OpenAI

    # Codyssey's API supports the OpenAI-compatible Chat Completions endpoint,
    # not necessarily the Responses API used in the original starter file.
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=40.0, max_retries=0)

    def invoke() -> str:
        try:
            result = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "너는 기술 뉴스 요약 편집자다. 입력된 본문에 있는 사실만 사용한다. "
                            "한국어로 핵심 사실을 정확히 3줄 이내로 요약한다. "
                            "각 줄은 완결된 한 문장이다. 추측, 광고, 출처 없는 숫자, 머리말은 쓰지 않는다."
                        ),
                    },
                    {"role": "user", "content": f"제목: {title}\n\n기사 본문:\n{body}"},
                ],
            )
        except APIStatusError as exc:
            if exc.status_code in (400, 401, 403, 404, 422):
                raise PermanentConfigError(
                    f"AI API HTTP {exc.status_code}: 모델명, 코디세이 키 종류, "
                    f"OPENAI_BASE_URL을 확인하세요 (현재 모델: {model})."
                ) from exc
            raise
        output = (result.choices[0].message.content or "").strip()
        if not output:
            raise ValueError("AI 빈 요약 응답")
        lines = [line.strip().lstrip("-• ") for line in output.splitlines() if line.strip()]
        if not lines:
            raise ValueError("요약 내용 없음")
        return "\n".join(lines[:3])

    return retry_call(invoke, "AI 뉴스 요약", max_retries=2)


def save_to_notion(data_source_id: str, token: str, article: Article, summary: str) -> str:
    props: dict[str, dict] = {
        "제목": {"title": [{"text": {"content": article.title[:1900]}}]},
        "요약": {"rich_text": [{"text": {"content": summary[:1900]}}]},
        "원문 링크": {"url": article.url},
    }
    if article.published_at:
        props["발행일시"] = {"date": {"start": article.published_at}}

    def invoke() -> dict:
        return notion_post(
            "/pages", token,
            {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": props},
        )
    page = retry_call(invoke, "Notion 저장", max_retries=2)
    return page.get("url", "")


def settings() -> dict[str, str]:
    load_dotenv()
    return {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "NOTION_TOKEN": os.getenv("NOTION_TOKEN", "").strip(),
        "NOTION_DATA_SOURCE_ID": os.getenv("NOTION_DATA_SOURCE_ID", "").strip(),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", "gpt-5-mini").strip(),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip(),
        "RSS_FEEDS": os.getenv("RSS_FEEDS", DEFAULT_FEED).strip(),
        "TOPIC_KEYWORDS": os.getenv("TOPIC_KEYWORDS", DEFAULT_KEYWORDS).strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="코디세이 Project B 뉴스 요약 자동화")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="RSS와 기사 본문만 시험. 유료 API/Notion 사용 안 함")
    mode.add_argument("--check", action="store_true", help="환경변수 유무만 점검. 키 값은 출력 안 함")
    mode.add_argument("--run", action="store_true", help="AI 요약 후 Notion에 1건 저장")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conf = settings()
    required = ("OPENAI_API_KEY", "NOTION_TOKEN", "NOTION_DATA_SOURCE_ID")
    if args.check:
        for k in required:
            LOG.info("%s = %s", k, "설정됨" if conf[k] else "미설정")
        LOG.info("OPENAI_BASE_URL = %s", conf["OPENAI_BASE_URL"])
        LOG.info("OPENAI_MODEL = %s", conf["OPENAI_MODEL"])
        return 0 if all(conf[k] for k in required) else 1
    if args.run and any(not conf[k] for k in required):
        LOG.error("필수 환경변수 미설정. python news_summary.py --check 실행")
        return 2

    feed_urls = [x.strip() for x in conf["RSS_FEEDS"].split(",") if x.strip()]
    keywords = [x.strip() for x in conf["TOPIC_KEYWORDS"].split(",") if x.strip()]
    if not feed_urls or not keywords:
        LOG.error("RSS_FEEDS 또는 TOPIC_KEYWORDS 설정이 비어 있음")
        return 2
    items = fetch_entries(feed_urls, keywords)
    LOG.info("필터를 통과한 기사: %d건", len(items))
    if not items:
        LOG.info("오늘은 조건에 맞는 기사가 없습니다. 정상 종료")
        return 0

    failed = False
    for article in items[:15]:
        try:
            if args.run and already_saved(conf["NOTION_DATA_SOURCE_ID"], conf["NOTION_TOKEN"], article.url):
                LOG.info("이미 저장된 기사 건너뜀: %s", article.title)
                continue
            body = extract_article_text(article)
            if args.dry_run:
                LOG.info("[시험 성공] 제목: %s", article.title)
                LOG.info("원문: %s", article.url)
                LOG.info("본문 추출: %d자 | 발행일: %s", len(body), article.published_at or "RSS 미제공")
                return 0
            summary = summarize_article(
                article.title, body, conf["OPENAI_API_KEY"], conf["OPENAI_MODEL"], conf["OPENAI_BASE_URL"]
            )
            saved_url = save_to_notion(conf["NOTION_DATA_SOURCE_ID"], conf["NOTION_TOKEN"], article, summary)
            LOG.info("[저장 성공] %s", article.title)
            LOG.info("노션 페이지: %s", saved_url)
            LOG.info("요약: %s", summary.replace("\n", " | "))
            return 0
        except PermanentConfigError as exc:
            LOG.error("API 설정 오류로 실행 중단 (불필요한 반복 요청 방지): %s", exc)
            return 1
        except Exception as exc:
            failed = True
            LOG.error("기사 처리 실패 (%s): %s", article.url, exc)
            # Prevent multiple billed AI summaries in one run after a Notion save error.
            if args.run and "Notion 저장" in str(exc):
                return 1
            continue
    LOG.warning("모든 후보가 중복이거나 처리 불가. 신규 저장 0건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
