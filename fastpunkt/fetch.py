"""Budgeted, polite, safe HTTP fetching with full provenance, plus record/replay for reproducible tests.

Every network read goes through `Fetcher.get`, which
- refuses non-http(s) URLs and hosts that resolve to private, loopback, link-local or reserved addresses
  (checked again on every redirect hop),
- enforces a global request budget and a wall-clock deadline,
- rate-limits per host and caps response size,
- records url, status, retrieval time, SHA-256 and byte count for evidence.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import USER_AGENT


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BudgetExhausted(Exception):
    pass


class UnsafeURL(Exception):
    pass


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str
    retrieved_at: str
    sha256: str
    elapsed_ms: int
    from_replay: bool = False

    def text(self) -> str:
        enc = "utf-8"
        ct = self.content_type.lower()
        if "charset=" in ct:
            enc = ct.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(enc, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


# Some sandboxes route all traffic through a fake-IP DNS proxy that answers every name with an address in
# 198.18.0.0/15. Set FASTPUNKT_FAKE_IP_DNS=1 only in such an environment: those addresses are then accepted,
# and DNS can no longer tell whether a candidate domain exists (the HTTP fetch decides instead).
FAKE_IP_DNS = os.environ.get("FASTPUNKT_FAKE_IP_DNS") == "1"
FAKE_NET = ipaddress.ip_network("198.18.0.0/15")


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if FAKE_IP_DNS and ip in FAKE_NET:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return True


def check_url(url: str, resolve=_host_is_public) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme not allowed: {p.scheme!r}")
    if not p.hostname or p.username or p.password:
        raise UnsafeURL("missing host or embedded credentials")
    if p.port not in (None, 80, 443):
        raise UnsafeURL(f"port not allowed: {p.port}")
    host = p.hostname.encode("idna").decode("ascii")
    if not resolve(host):
        raise UnsafeURL(f"host does not resolve to a public address: {host}")
    return url


def resolves(host: str) -> bool:
    """DNS-only check (no HTTP request) used to drop non-existent candidate domains before fetching."""
    return _host_is_public(host)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@dataclass
class Fetcher:
    max_requests: int = 2000
    deadline: float | None = None  # epoch seconds
    timeout: float = 12.0
    max_bytes: int = 2_000_000
    per_host_interval: float = 0.6
    record_dir: Path | None = None
    replay_dir: Path | None = None
    resolve: object = staticmethod(_host_is_public)
    requests_made: int = 0
    log: list = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.Lock()
        self._host_next: dict[str, float] = {}
        self._opener = urllib.request.build_opener(_NoRedirect)
        for d in (self.record_dir, self.replay_dir):
            if d:
                Path(d).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:24]

    def resolves(self, host: str) -> bool:
        """DNS check with record/replay so offline replays make no network calls at all."""
        dns_file = Path(self.replay_dir or self.record_dir or ".") / "dns.json"
        if self.replay_dir:
            return json.loads(dns_file.read_text()).get(host, False) if dns_file.exists() else False
        ok = resolves(host)
        if self.record_dir:
            with self._lock:
                d = json.loads(dns_file.read_text()) if dns_file.exists() else {}
                d[host] = ok
                dns_file.write_text(json.dumps(d, indent=0, sort_keys=True))
        return ok

    def remaining(self) -> int:
        return self.max_requests - self.requests_made

    def time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.time()

    def _reserve(self):
        with self._lock:
            if self.requests_made >= self.max_requests:
                raise BudgetExhausted("request budget exhausted")
            if self.deadline is not None and time.time() > self.deadline:
                raise BudgetExhausted("time budget exhausted")
            self.requests_made += 1

    def _wait_host(self, host: str):
        with self._lock:
            now = time.time()
            t = max(now, self._host_next.get(host, 0.0))
            self._host_next[host] = t + self.per_host_interval
        if t > now:
            time.sleep(t - now)

    def get(self, url: str, accept: str = "*/*", max_redirects: int = 5) -> Response:
        if self.replay_dir:
            f = Path(self.replay_dir) / f"{self._key(url)}.json"
            if not f.exists():
                raise FileNotFoundError(f"not in replay set: {url}")
            d = json.loads(f.read_text())
            body = gzip.decompress(base64.b64decode(d["body_b64gz"]))
            with self._lock:
                self.requests_made += 1
            return Response(url, d["final_url"], d["status"], body, d["content_type"], d["retrieved_at"], d["sha256"], 0, True)
        t0 = time.time()
        cur = url
        for _ in range(max_redirects + 1):
            check_url(cur, self.resolve)
            self._reserve()
            host = urllib.parse.urlsplit(cur).hostname
            self._wait_host(host)
            req = urllib.request.Request(cur, headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "nb,no;q=0.9,en;q=0.7"})
            # The public registry (data.brreg.no) is the backbone of every envelope, so transient
            # timeouts and 429/5xx there get two retries with backoff. Company sites get none.
            tries = 3 if (host or "").endswith("brreg.no") else 1
            for attempt in range(tries):
                try:
                    resp = self._opener.open(req, timeout=self.timeout)
                    status, headers = resp.status, resp.headers
                    body = resp.read(self.max_bytes + 1)[: self.max_bytes]
                except urllib.error.HTTPError as e:
                    status, headers = e.code, e.headers
                    body = e.read(self.max_bytes) if e.fp else b""
                except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
                    if attempt + 1 >= tries:
                        raise
                    time.sleep(1.5 * (attempt + 1)); self._reserve()
                    continue
                if status in (429, 500, 502, 503, 504) and attempt + 1 < tries:
                    time.sleep(1.5 * (attempt + 1)); self._reserve()
                    continue
                break
            if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                cur = urllib.parse.urljoin(cur, headers["Location"])
                self.log.append({"url": cur, "status": status, "redirect": True})
                continue
            r = Response(url, cur, status, body, headers.get("Content-Type", ""), utcnow(),
                         hashlib.sha256(body).hexdigest(), int((time.time() - t0) * 1000))
            self.log.append({"url": url, "final_url": cur, "status": status, "bytes": len(body), "ms": r.elapsed_ms})
            if self.record_dir:
                (Path(self.record_dir) / f"{self._key(url)}.json").write_text(json.dumps({
                    "url": url, "final_url": cur, "status": status, "content_type": r.content_type,
                    "retrieved_at": r.retrieved_at, "sha256": r.sha256,
                    "body_b64gz": base64.b64encode(gzip.compress(body, mtime=0)).decode()}))
            return r
        raise UnsafeURL("too many redirects")
