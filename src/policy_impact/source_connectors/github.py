"""Unauthenticated public repository discovery and bounded primary evidence reads."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable
from urllib.parse import quote, urlencode

from policy_impact.source_connectors.discovery import bounded_int, query_urls
from policy_impact.source_connectors.http_client import domain_allowed


def collect_github(
    source: dict[str, Any], plan: dict[str, Any], limit: int, *, fetch: Callable,
    make_item: Callable,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: dict[str, Any] = {"request_count": 0, "raw_count": 0, "errors": [], "query_urls": [],
                               "repository_count": 0, "body_fetch_count": 0}
    repositories: dict[str, tuple[dict[str, Any], str, str]] = {}
    releases: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []

    def get(url: str):
        details["request_count"] += 1
        return fetch(url, allowed_domains=list(source.get("allowed_domains") or []),
                     timeout_seconds=8, retries=0, max_bytes=2_000_000)

    for url in query_urls(source, plan, limit):
        details["query_urls"].append(url)
        try:
            response = get(url)
            payload = json.loads(response.body)
            if payload.get("incomplete_results"):
                details["errors"].append({"url": url, "error": "GitHub returned incomplete search results"})
            rows = payload.get("items")
            if not isinstance(rows, list):
                raise ValueError("GitHub search response does not contain an items list")
            details["raw_count"] += len(rows)
            for repo in rows:
                name = str(repo.get("full_name") or "")
                if (repo.get("private") is True or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)
                        or not domain_allowed(str(repo.get("html_url") or ""), ["github.com"])):
                    continue
                if len(repositories) < limit:
                    repositories.setdefault(name, (repo, response.retrieved_at, url))
        except Exception as exc:  # noqa: BLE001
            details["errors"].append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    details["repository_count"] = len(repositories)
    detail_limit = min(limit, bounded_int(source.get("max_repo_details"), 4, 6, minimum=0))
    for index, (name, (repo, retrieved_at, query_url)) in enumerate(repositories.items()):
        repo_url = str(repo["html_url"])
        item = make_item(source, name, "", repo_url, str(repo.get("description") or ""), retrieved_at)
        item.update(evidence_kind="repository_metadata", published_at_basis="unknown",
                    repository=name, repository_created_at=str(repo.get("created_at") or ""),
                    repository_pushed_at=str(repo.get("pushed_at") or ""),
                    updated_at=str(repo.get("updated_at") or ""),
                    discovery_query_url=query_url, topics=list(repo.get("topics") or []),
                    limitations=["Repository activity and creation dates are not a publication date."])
        if index < detail_limit:
            endpoint = "https://api.github.com/repos/" + quote(name, safe="/")
            readme_url = endpoint + "/readme"
            try:
                details["body_fetch_count"] += 1
                response = get(readme_url)
                readme = json.loads(response.body)
                if readme.get("encoding") != "base64" or not isinstance(readme.get("content"), str):
                    raise ValueError("README response has no inline base64 content")
                text = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
                item.update(text=text[:24_000], evidence_kind="repository_readme",
                            body_truncated=len(text) > 24_000, readme_sha=str(readme.get("sha") or ""),
                            readme_url=str(readme.get("html_url") or repo_url),
                            body_retrieved_at=response.retrieved_at, response_sha256=response.sha256,
                            limitations=["README is a current repository document, not a dated release."])
                if text.strip():
                    item["summary"] = text[:6000]
            except Exception as exc:  # noqa: BLE001
                details["errors"].append({"url": readme_url, "error": f"{type(exc).__name__}: {exc}"})
            release_url = endpoint + "/releases?" + urlencode({"per_page": min(5, limit)})
            try:
                details["body_fetch_count"] += 1
                response = get(release_url)
                rows = json.loads(response.body)
                if not isinstance(rows, list):
                    raise ValueError("GitHub releases response is not a list")
                details["raw_count"] += len(rows)
                for release in rows[:5]:
                    if release.get("draft") or not domain_allowed(str(release.get("html_url") or ""), ["github.com"]):
                        continue
                    title = name + " " + str(release.get("name") or release.get("tag_name") or "release")
                    release_item = make_item(source, title, str(release.get("published_at") or ""),
                                             str(release["html_url"]), str(release.get("body") or ""),
                                             response.retrieved_at)
                    release_item.update(evidence_kind="release_notes", repository=name,
                                        published_at_basis="github_release_published_at",
                                        discovery_query_url=query_url, response_sha256=response.sha256,
                                        text=str(release.get("body") or "")[:24_000],
                                        body_truncated=len(str(release.get("body") or "")) > 24_000)
                    releases.append(release_item)
            except Exception as exc:  # noqa: BLE001
                details["errors"].append({"url": release_url, "error": f"{type(exc).__name__}: {exc}"})
        metadata.append(item)
    # Dated release evidence gets first claim on the bounded candidate budget.
    return (releases + metadata)[:limit], details
