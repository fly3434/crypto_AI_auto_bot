import json
import time
from datetime import datetime
from datetime import timezone

from src.news_context_builder import NewsContextBuilder
from src.news_fetcher import NewsFetcher
from src.news_fetcher import effective_news_interval_minutes


class FakeResponse:
    def __init__(self, payload=None, content=b"") -> None:
        self.payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        return FakeResponse(
            payload={
                "news": [
                    {
                        "title": "Bitcoin ETF inflows surge as BTC rallies",
                        "source": "Example News",
                        "url": "https://example.test/btc-etf",
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "summary": "Fresh ETF inflows supported market sentiment. Traders still watch risk limits.",
                    }
                ]
            }
        )


class PartiallyFailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("slow source")
        return FakeResponse(
            payload={
                "news": [
                    {
                        "title": "Ethereum upgrade boosts ETH sentiment",
                        "source": "Backup News",
                        "url": "https://example.test/eth-upgrade",
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "summary": "A protocol upgrade improved market sentiment.",
                    }
                ]
            }
        )


def test_effective_news_interval_uses_trigger_when_slower_than_minimum():
    assert effective_news_interval_minutes(360, {"min_fetch_interval_minutes": 30}) == 360


def test_effective_news_interval_keeps_minimum_when_trigger_is_fast():
    assert effective_news_interval_minutes(5, {"min_fetch_interval_minutes": 30}) == 30


def test_news_fetcher_uses_cache_until_effective_interval(tmp_path):
    session = FakeSession()
    fetcher = NewsFetcher(
        {
            "cache_path": str(tmp_path / "news.json"),
            "sources": [{"name": "fake", "type": "json", "url": "https://example.test/news"}],
            "min_fetch_interval_minutes": 30,
            "max_items": 5,
        },
        session=session,
    )

    first = fetcher.get_news(["BTCUSDT"], trigger_interval_minutes=5)
    second = fetcher.get_news(["BTCUSDT"], trigger_interval_minutes=5)

    assert session.calls == 1
    assert first["status"] == "fresh"
    assert second["status"] == "cache"
    assert second["fetch_interval_minutes"] == 30
    assert second["items"][0]["assets"] == ["BTC"]


def test_news_fetcher_skips_failed_source_and_keeps_successful_sources(tmp_path):
    session = PartiallyFailingSession()
    fetcher = NewsFetcher(
        {
            "cache_path": str(tmp_path / "news.json"),
            "sources": [
                {"name": "slow", "type": "json", "url": "https://example.test/slow"},
                {"name": "backup", "type": "json", "url": "https://example.test/backup"},
            ],
        },
        session=session,
    )

    context = fetcher.get_news(["ETHUSDT"], trigger_interval_minutes=30)

    assert context["status"] == "fresh"
    assert context["items"][0]["assets"] == ["ETH"]
    assert context["source_errors"][0]["source"] == "slow"


def test_news_fetcher_refetches_when_cache_is_older_than_slow_trigger(tmp_path):
    cache_path = tmp_path / "news.json"
    cache_path.write_text(json.dumps({"fetched_at": time.time() - 7200, "items": []}), encoding="utf-8")
    session = FakeSession()
    fetcher = NewsFetcher(
        {
            "cache_path": str(cache_path),
            "sources": [{"name": "fake", "type": "json", "url": "https://example.test/news"}],
            "min_fetch_interval_minutes": 30,
        },
        session=session,
    )

    context = fetcher.get_news(["BTCUSDT"], trigger_interval_minutes=60)

    assert session.calls == 1
    assert context["status"] == "fresh"
    assert context["fetch_interval_minutes"] == 60


def test_news_context_builder_returns_compact_prompt_items(tmp_path):
    session = FakeSession()
    builder = NewsContextBuilder(
        {
            "cache_path": str(tmp_path / "news.json"),
            "sources": [{"name": "fake", "type": "json", "url": "https://example.test/news"}],
            "max_prompt_items": 1,
        },
        fetcher=NewsFetcher(
            {
                "cache_path": str(tmp_path / "news.json"),
                "sources": [{"name": "fake", "type": "json", "url": "https://example.test/news"}],
            },
            session=session,
        ),
    )

    context = builder.build(["BTCUSDT"], trigger_interval_minutes=30)

    assert len(context["items"]) == 1
    assert "url" not in context["items"][0]
    assert context["items"][0]["event_type"] == "etf"
    assert context["items"][0]["sentiment"] == "bullish"
