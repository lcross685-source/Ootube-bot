"""Minimal RSS/Atom parsing.

Implemented on the standard library rather than feedparser: the only fields
this pipeline needs are title, link, date, and summary, and the publication
date matters more than anything else here - it becomes ``TrendSignal.event_at``
and therefore decides whether a topic survives the freshness gate.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

log = logging.getLogger(__name__)

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ht": "https://trends.google.com/trending/rss",
}


def parse_date(value: str | None) -> datetime | None:
    """Parse the date formats that appear in real feeds."""
    if not value:
        return None
    value = value.strip()
    try:  # RFC 822, the RSS default
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    cleaned = value.replace("Z", "+00:00")
    try:  # ISO 8601, the Atom default
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _text(el: Any, *paths: str) -> str:
    for path in paths:
        found = el.find(path, _NS)
        if found is not None and (found.text or "").strip():
            return (found.text or "").strip()
    return ""


def _link(el: Any) -> str:
    link = el.find("link")
    if link is not None:
        if (link.text or "").strip():
            return (link.text or "").strip()
        href = link.get("href")
        if href:
            return href
    alt = el.find("atom:link", _NS)
    if alt is not None and alt.get("href"):
        return alt.get("href", "")
    return ""


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml_text: str, max_items: int = 50) -> list[dict[str, Any]]:
    """Return a list of ``{title, link, published, summary, extra}`` dicts."""
    if not xml_text:
        return []
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8", errors="replace"))
    except ElementTree.ParseError as exc:
        log.warning("Feed parse error: %s", exc)
        return []

    items = root.findall(".//item") or root.findall(".//atom:entry", _NS)
    out: list[dict[str, Any]] = []
    for el in items[:max_items]:
        title = _text(el, "title", "atom:title")
        if not title:
            continue
        published = parse_date(
            _text(el, "pubDate", "published", "atom:published", "atom:updated", "dc:date")
        )
        summary = strip_html(
            _text(el, "description", "summary", "atom:summary", "content", "atom:content")
        )
        # Google Trends RSS carries approximate search volume and the news
        # articles driving the spike.
        extra: dict[str, Any] = {}
        traffic = _text(el, "ht:approx_traffic")
        if traffic:
            extra["approx_traffic"] = traffic
        news_titles = [
            (n.text or "").strip()
            for n in el.findall("ht:news_item/ht:news_item_title", _NS)
            if (n.text or "").strip()
        ]
        if news_titles:
            extra["news_titles"] = news_titles
            if not summary:
                summary = "; ".join(news_titles[:3])
        news_dates = [
            parse_date((n.text or "").strip())
            for n in el.findall("ht:news_item/ht:news_item_published", _NS)
        ]
        news_dates = [d for d in news_dates if d]
        if news_dates and not published:
            published = max(news_dates)

        out.append(
            {
                "title": title,
                "link": _link(el),
                "published": published,
                "summary": summary,
                "extra": extra,
            }
        )
    return out


def parse_traffic(value: str | None) -> float:
    """Turn Google Trends' ``"50,000+"`` / ``"2M+"`` strings into a number."""
    if not value:
        return 0.0
    text = value.strip().lower().replace(",", "").replace("+", "")
    mult = 1.0
    if text.endswith("k"):
        mult, text = 1_000.0, text[:-1]
    elif text.endswith("m"):
        mult, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return 0.0
