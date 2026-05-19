from __future__ import annotations

from typing import Any

from .news_fetcher import NewsFetcher


class NewsContextBuilder:
    def __init__(self, settings: dict[str, Any] | None = None, fetcher: NewsFetcher | None = None) -> None:
        self.settings = settings or {}
        self.fetcher = fetcher or NewsFetcher(self.settings)

    def build(self, symbols: list[str], trigger_interval_minutes: float) -> dict[str, Any]:
        context = self.fetcher.get_news(symbols, trigger_interval_minutes)
        max_items = int(self.settings.get("max_prompt_items", self.settings.get("max_items", 10)))
        context["items"] = [
            {
                "title": item.get("title"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "assets": item.get("assets", []),
                "event_type": item.get("event_type", "market"),
                "sentiment": item.get("sentiment", "neutral"),
                "importance": item.get("importance", 1),
                "summary": item.get("summary"),
            }
            for item in context.get("items", [])[:max_items]
            if isinstance(item, dict)
        ]
        context["usage_note"] = (
            "News is compact market context only. Do not treat news as a standalone trading signal; "
            "technical features, risk limits, and position management remain primary."
        )
        return context
