from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import Settings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 4) -> list[SearchResult]: ...


class TavilySearch:
    def __init__(self, api_key: str, timeout: float) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": self.api_key, "query": query, "max_results": limit},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            SearchResult(item.get("title", ""), item.get("url", ""), item.get("content", ""))
            for item in payload.get("results", [])
        ]


class BraveSearch:
    def __init__(self, api_key: str, timeout: float) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            SearchResult(
                item.get("title", ""),
                item.get("url", ""),
                item.get("description", ""),
            )
            for item in (payload.get("web") or {}).get("results", [])
        ]


class SerperSearch:
    def __init__(self, api_key: str, timeout: float) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                json={"q": query, "num": limit},
                headers={"X-API-KEY": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            SearchResult(item.get("title", ""), item.get("url", ""), item.get("snippet", ""))
            for item in payload.get("organic", [])
        ]


class SearXNGSearch:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "language": "ru"},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            SearchResult(item.get("title", ""), item.get("url", ""), item.get("content", ""))
            for item in payload.get("results", [])[:limit]
        ]


class MockSearch:
    async def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        return [
            SearchResult(
                "Как варить макароны: время приготовления",
                "https://example.org/pasta",
                "Обычные макароны варят 8–12 минут до состояния аль денте, затем откидывают на дуршлаг.",
            ),
            SearchResult(
                "Как убрать нагар с кастрюли",
                "https://example.org/cleaning",
                "Наполните кастрюлю водой, добавьте лимонную кислоту или пищевую соду и прокипятите 10 минут.",
            ),
        ][:limit]


def build_search_provider(settings: Settings) -> SearchProvider:
    kind = settings.search_provider.lower()
    if kind == "tavily":
        return TavilySearch(settings.search_api_key, settings.search_timeout)
    if kind == "brave":
        return BraveSearch(settings.search_api_key, settings.search_timeout)
    if kind == "serper":
        return SerperSearch(settings.search_api_key, settings.search_timeout)
    if kind == "searxng":
        return SearXNGSearch(settings.search_base_url, settings.search_timeout)
    if kind == "mock":
        return MockSearch()
    raise ValueError(f"Unknown search provider: {kind}")


__all__: list[Any] = [
    "SearchResult",
    "SearchProvider",
    "TavilySearch",
    "BraveSearch",
    "SerperSearch",
    "SearXNGSearch",
    "MockSearch",
    "build_search_provider",
]
