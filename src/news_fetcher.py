from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests


DEFAULT_SOURCES = [
    {
        "name": "cryptocurrency.cv",
        "type": "json",
        "url": "https://cryptocurrency.cv/api/news",
    },
    {
        "name": "Cointelegraph",
        "type": "rss",
        "url": "https://cointelegraph.com/rss",
    },
    {
        "name": "CoinDesk",
        "type": "rss",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
]


@dataclass(frozen=True)
class NewsItem:
    id: str
    title: str
    source: str
    url: str
    published_at: str
    summary: str
    assets: list[str]
    event_type: str
    sentiment: str
    importance: int


class NewsFetcher:
    def __init__(self, settings: dict[str, Any] | None = None, session: requests.Session | None = None) -> None:
        self.settings = settings or {}
        self.session = session or requests.Session()
        self.enabled = bool(self.settings.get("enabled", True))
        self.cache_path = Path(str(self.settings.get("cache_path") or "/tmp/crypto_ai_news_cache.json"))
        self.timeout_seconds = float(self.settings.get("timeout_seconds", 5))
        self.freshness_hours = float(self.settings.get("freshness_hours", 24))
        self.max_items = int(self.settings.get("max_items", 10))
        self.sources = self.settings.get("sources") if isinstance(self.settings.get("sources"), list) else DEFAULT_SOURCES
        self._last_source_errors: list[dict[str, str]] = []

    def get_news(self, symbols: list[str], trigger_interval_minutes: float) -> dict[str, Any]:
        if not self.enabled:
            return self._context([], "disabled", trigger_interval_minutes)

        fetch_interval = effective_news_interval_minutes(trigger_interval_minutes, self.settings)
        cached = self._read_cache()
        if cached and not self._cache_expired(cached, fetch_interval):
            items = [NewsItem(**item) for item in cached.get("items", []) if isinstance(item, dict)]
            return self._context(items[: self.max_items], "cache", trigger_interval_minutes, fetch_interval)

        try:
            items = self.fetch(symbols)
            self._write_cache(items)
            context = self._context(items[: self.max_items], "fresh", trigger_interval_minutes, fetch_interval)
            if self._last_source_errors:
                context["source_errors"] = self._last_source_errors
            return context
        except Exception as exc:
            if cached:
                items = [NewsItem(**item) for item in cached.get("items", []) if isinstance(item, dict)]
                context = self._context(items[: self.max_items], "stale_cache", trigger_interval_minutes, fetch_interval)
                context["error"] = repr(exc)
                return context
            context = self._context([], "error", trigger_interval_minutes, fetch_interval)
            context["error"] = repr(exc)
            return context

    def fetch(self, symbols: list[str]) -> list[NewsItem]:
        raw_items: list[dict[str, Any]] = []
        source_errors: list[dict[str, str]] = []
        for source in self.sources:
            if not isinstance(source, dict) or not source.get("url"):
                continue
            source_type = str(source.get("type") or "rss").lower()
            try:
                if source_type == "json":
                    raw_items.extend(self._fetch_json_source(source))
                else:
                    raw_items.extend(self._fetch_rss_source(source))
            except Exception as exc:
                source_errors.append(
                    {
                        "source": str(source.get("name") or source.get("url") or "unknown"),
                        "error": repr(exc),
                    }
                )
                continue
        self._last_source_errors = source_errors
        return self._normalize_items(raw_items, symbols)

    def _fetch_json_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        response = self.session.get(str(source["url"]), timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        records = _extract_json_records(payload)
        items = []
        for record in records:
            if not isinstance(record, dict):
                continue
            title = _first_text(record, ["title", "headline", "name"])
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "source": _first_text(record, ["source", "source_name", "provider"]) or str(source.get("name") or ""),
                    "url": _first_text(record, ["url", "link"]),
                    "published_at": _first_text(record, ["published_at", "publishedAt", "pubDate", "date", "created_at"]),
                    "summary": _first_text(record, ["summary", "description", "body", "content"]),
                }
            )
        return items

    def _fetch_rss_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        response = self.session.get(str(source["url"]), timeout=self.timeout_seconds)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = []
        for element in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = _xml_text(element, "title")
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "source": str(source.get("name") or ""),
                    "url": _xml_text(element, "link"),
                    "published_at": _xml_text(element, "pubDate") or _xml_text(element, "published") or _xml_text(element, "updated"),
                    "summary": _xml_text(element, "description") or _xml_text(element, "summary"),
                }
            )
        return items

    def _normalize_items(self, raw_items: list[dict[str, Any]], symbols: list[str]) -> list[NewsItem]:
        seen: set[str] = set()
        cutoff = time.time() - self.freshness_hours * 3600
        items: list[NewsItem] = []
        for raw in raw_items:
            title = _clean_text(str(raw.get("title") or ""))
            if not title:
                continue
            published_ts = _parse_timestamp(raw.get("published_at")) or time.time()
            if published_ts < cutoff:
                continue
            url = str(raw.get("url") or "").strip()
            fingerprint = _fingerprint(title, url)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            source = _clean_text(str(raw.get("source") or "Unknown"))[:80]
            summary = _compact_summary(str(raw.get("summary") or title))
            assets = _detect_assets(" ".join([title, summary]), symbols)
            event_type = _event_type(title, summary)
            sentiment = _sentiment(title, summary)
            importance = _importance(title, summary, assets, published_ts)
            items.append(
                NewsItem(
                    id=fingerprint,
                    title=title[:180],
                    source=source,
                    url=url,
                    published_at=datetime.fromtimestamp(published_ts, timezone.utc).isoformat(),
                    summary=summary,
                    assets=assets,
                    event_type=event_type,
                    sentiment=sentiment,
                    importance=importance,
                )
            )
        items.sort(key=lambda item: (item.importance, item.published_at), reverse=True)
        return items

    def _read_cache(self) -> dict[str, Any] | None:
        if not self.cache_path.exists():
            return None
        try:
            with self.cache_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _write_cache(self, items: list[NewsItem]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), "items": [asdict(item) for item in items]}
        with self.cache_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _cache_expired(self, cached: dict[str, Any], fetch_interval_minutes: float) -> bool:
        fetched_at = float(cached.get("fetched_at") or 0)
        return time.time() - fetched_at >= fetch_interval_minutes * 60

    def _context(
        self,
        items: list[NewsItem],
        status: str,
        trigger_interval_minutes: float,
        fetch_interval_minutes: float | None = None,
    ) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": status,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "trigger_interval_minutes": round(trigger_interval_minutes, 2),
            "fetch_interval_minutes": round(fetch_interval_minutes or effective_news_interval_minutes(trigger_interval_minutes, self.settings), 2),
            "items": [asdict(item) for item in items],
        }


def effective_news_interval_minutes(trigger_interval_minutes: float, settings: dict[str, Any] | None = None) -> float:
    settings = settings or {}
    min_interval = float(settings.get("min_fetch_interval_minutes", 30))
    min_interval = max(15.0, min_interval)
    return max(float(trigger_interval_minutes), min_interval)


def _extract_json_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("news", "articles", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _first_text(record: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
        if isinstance(value, dict):
            nested = _first_text(value, ["name", "title"])
            if nested:
                return nested
    return ""


def _xml_text(element: ET.Element, tag: str) -> str:
    found = element.find(tag)
    if found is None:
        found = element.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    if found is None:
        return ""
    if tag == "link" and "href" in found.attrib:
        return found.attrib["href"].strip()
    return _clean_text("".join(found.itertext()))


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _detect_assets(text: str, symbols: list[str]) -> list[str]:
    upper = text.upper()
    assets = []
    for symbol in symbols:
        base = re.sub(r"(USDT|USD|BUSD|USDC)$", "", symbol.upper())
        aliases = {base}
        if base == "BTC":
            aliases.add("BITCOIN")
        elif base == "ETH":
            aliases.add("ETHEREUM")
        elif base == "BNB":
            aliases.add("BINANCE")
        elif base == "DOGE":
            aliases.add("DOGECOIN")
        if any(re.search(rf"\b{re.escape(alias)}\b", upper) for alias in aliases):
            assets.append(base)
    return assets[:8]


def _event_type(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    mapping = {
        "regulation": ["sec", "cftc", "regulat", "lawsuit", "court", "ban", "policy"],
        "security": ["hack", "exploit", "stolen", "breach", "phishing", "vulnerability"],
        "macro": ["fed", "inflation", "rate cut", "rate hike", "cpi", "jobs", "treasury"],
        "etf": ["etf", "fund flow", "inflow", "outflow"],
        "listing": ["listing", "delisting", "listed"],
        "protocol": ["upgrade", "fork", "mainnet", "staking", "airdrop"],
    }
    for event_type, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            return event_type
    return "market"


def _sentiment(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    bearish = ["hack", "exploit", "ban", "lawsuit", "outflow", "crash", "liquidation", "selloff", "fraud"]
    bullish = ["approval", "inflow", "rally", "surge", "record high", "partnership", "upgrade", "adoption"]
    bear_count = sum(1 for keyword in bearish if keyword in text)
    bull_count = sum(1 for keyword in bullish if keyword in text)
    if bull_count > bear_count:
        return "bullish"
    if bear_count > bull_count:
        return "bearish"
    return "neutral"


def _importance(title: str, summary: str, assets: list[str], published_ts: float) -> int:
    text = f"{title} {summary}".lower()
    score = 1 + min(2, len(assets))
    high_impact = ["sec", "fed", "etf", "hack", "exploit", "lawsuit", "binance", "coinbase", "liquidation"]
    score += sum(1 for keyword in high_impact if keyword in text)
    if time.time() - published_ts <= 3 * 3600:
        score += 1
    return max(1, min(5, score))


def _compact_summary(text: str) -> str:
    cleaned = _clean_text(re.sub(r"<[^>]+>", " ", text))
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(sentence for sentence in sentences[:2] if sentence)[:280]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fingerprint(title: str, url: str) -> str:
    key = f"{re.sub(r'[^a-z0-9]+', '', title.lower())}|{url.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
