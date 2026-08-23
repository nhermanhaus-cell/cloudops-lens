from __future__ import annotations

from cloudops_lens.refresh import _fetch_github_pages


class Page:
    def __init__(self, rows: list[dict], next_url: str | None = None) -> None:
        self.rows = rows
        self.links = {"next": {"url": next_url}} if next_url else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self.rows


class Session:
    def __init__(self, pages: list[Page]) -> None:
        self.pages = iter(pages)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs) -> Page:
        self.calls.append({"url": url, **kwargs})
        return next(self.pages)


def test_github_pagination_follows_next_link_and_honors_limit() -> None:
    session = Session(
        [
            Page([{"id": 1}], "https://api.github.test/page-2"),
            Page([{"id": 2}], "https://api.github.test/page-3"),
            Page([{"id": 3}]),
        ]
    )
    rows = _fetch_github_pages(session, "https://api.github.test/page-1", max_pages=2)
    assert rows == [{"id": 1}, {"id": 2}]
    assert [call["url"] for call in session.calls] == [
        "https://api.github.test/page-1",
        "https://api.github.test/page-2",
    ]
    assert session.calls[0]["params"] == {"per_page": 100}
    assert session.calls[1]["params"] is None
