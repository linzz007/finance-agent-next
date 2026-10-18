"""Small, dependency-free HTTP client with source-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import socket
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from policy_impact.harness.research_budget import CURRENT, source_timeout, tighten_response_timeout


USER_AGENT = "PolicyImpactWorkbench/0.1 (+local primary-source monitor)"


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    content_type: str
    body: bytes
    retrieved_at: str
    sha256: str


def public_url(url: str) -> bool:
    """Cheap URL validation for evidence links; fetching also checks DNS/redirects."""
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443} or "\\" in url
                or any(ord(char) < 32 for char in url)):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host
    except ValueError:
        return False


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    if not public_url(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname.lower().rstrip(".")
    return any(
        host == domain.lower().rstrip(".") or host.endswith("." + domain.lower().rstrip("."))
        for domain in allowed_domains if isinstance(domain, str) and domain.strip(".")
    )


def _validate_destination(url: str, allowed_domains: list[str] | None) -> None:
    if not public_url(url) or (allowed_domains is not None and not domain_allowed(url, allowed_domains)):
        raise ValueError(f"source URL is outside the public allowlist: {url}")
    parsed = urlparse(url)
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("source hostname resolves to a non-public address")


class _AllowedRedirects(HTTPRedirectHandler):
    max_redirections = 4

    def __init__(self, allowed_domains: list[str] | None) -> None:
        self.allowed_domains = allowed_domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source_timeout(getattr(req, "timeout", 18.0))
        _validate_destination(newurl, self.allowed_domains)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(
    url: str,
    *,
    allowed_domains: list[str] | None,
    timeout_seconds: float = 18.0,
    retries: int = 1,
    retry_delay_seconds: float = 0.25,
    max_bytes: int = 3_000_000,
) -> FetchResponse:
    if not public_url(url) or (allowed_domains is not None and not domain_allowed(url, allowed_domains)):
        raise ValueError(f"source URL is outside the public allowlist: {url}")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        effective_timeout = source_timeout(timeout_seconds)
        try:
            _validate_destination(url, allowed_domains)
            effective_timeout = source_timeout(effective_timeout)
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/atom+xml, application/rss+xml, application/json, text/html;q=0.9, */*;q=0.5",
                },
            )
            opener = build_opener(_AllowedRedirects(allowed_domains))
            with opener.open(request, timeout=effective_timeout) as response:
                if not public_url(response.geturl()) or (
                    allowed_domains is not None and not domain_allowed(response.geturl(), allowed_domains)
                ):
                    raise ValueError("source redirected outside the public allowlist")
                if CURRENT.get() is not None and hasattr(response, "read1"):
                    chunks, size = [], 0
                    while True:
                        remaining = source_timeout(effective_timeout)
                        tighten_response_timeout(response, remaining, remaining_seconds=remaining)
                        chunk = response.read1(min(65536, max_bytes + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(f"source response exceeds {max_bytes} bytes")
                    body = b"".join(chunks)
                else:
                    body = response.read(max_bytes + 1)
                source_timeout(effective_timeout)
                if len(body) > max_bytes:
                    raise ValueError(f"source response exceeds {max_bytes} bytes")
                return FetchResponse(
                    url=response.geturl(),
                    status=int(getattr(response, "status", 200)),
                    content_type=str(response.headers.get("content-type") or ""),
                    body=body,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                delay = min(3.0, max(0.0, retry_delay_seconds)) * (attempt + 1)
                if CURRENT.get() is not None and delay >= source_timeout(timeout_seconds):
                    raise TimeoutError("source retry cannot spend finalization reserve") from exc
                time.sleep(delay)
    assert last_error is not None
    raise last_error
