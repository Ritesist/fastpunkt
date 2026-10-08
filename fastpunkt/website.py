"""Find and verify the company's own website, then read company-owned facts from it.

Identity rule (precision first):
- the page shows this organisation number                        -> verified (orgnr_on_site)
- the registry lists this site for the entity                    -> verified (registry_listed)
- otherwise (registry email domain or name-derived domain)       -> published only if the site names the company
  and a TypeSafe identity judgment is >= 0.9; else kept as an unpublished `ambiguous` candidate.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from . import orgnr as on
from .fetch import BudgetExhausted, Fetcher, Response, UnsafeURL, resolves

FREE_MAIL = {"gmail.com", "hotmail.com", "outlook.com", "live.com", "live.no", "yahoo.com", "yahoo.no", "icloud.com", "me.com",
             "online.no", "frisurf.no", "start.no", "broadpark.no", "getmail.no", "hotmail.no", "msn.com", "c2i.net", "lyse.net",
             "altibox.no", "telia.no", "netcom.no", "chello.no", "bluezone.no", "ebnett.no", "tele2.no", "mimer.no", "proton.me",
             "protonmail.com", "aol.com", "gmx.com", "mail.com", "outlook.no", "post.com", "haugnett.no", "enivest.net"}
LEGAL_SUFFIX = r"\b(as|asa|ans|da|sa|ba|enk|nuf|sti|brl|ba|ks|iks|sf|kf|fli|esek|sam)\b"
SOCIAL = [
    ("linkedin", re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/(company|school|showcase)/[^/?#]+", re.I)),
    ("facebook", re.compile(r"^https?://(www\.|m\.|nb-no\.)?facebook\.com/(?!sharer|share|dialog|plugins|tr\b|groups/?$)[^?#]+", re.I)),
    ("instagram", re.compile(r"^https?://(www\.)?instagram\.com/(?!p/|explore/)[A-Za-z0-9_.]+", re.I)),
    ("youtube", re.compile(r"^https?://(www\.)?youtube\.com/(channel/|c/|user/|@)[^/?#]+", re.I)),
    ("x", re.compile(r"^https?://(www\.)?(twitter|x)\.com/(?!intent|share|home)[A-Za-z0-9_]+/?$", re.I)),
    ("tiktok", re.compile(r"^https?://(www\.)?tiktok\.com/@[^/?#]+", re.I)),
]
ATS = re.compile(r"(webcruiter|jobylon|teamtailor|recman|hrmanager|easycruit|reachmee|varbi|talentech|jobbnorge|finn\.no/job|arbeidsplassen\.nav\.no)", re.I)
KEYWORDS = {
    "careers": re.compile(r"(karriere|ledige[- ]stillinger|stilling|jobb|career|jobs|vacanc|join-us|bli-med)", re.I),
    "news": re.compile(r"(nyhet|aktuelt|news|presse|press|blogg|blog|artikler)", re.I),
    "about": re.compile(r"(om-oss|om oss|about|hvem-er-vi|historie|selskapet)", re.I),
    "contact": re.compile(r"(kontakt|contact)", re.I),
}
MONTHS = {"januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
          "oktober": 10, "november": 11, "desember": 12, "january": 1, "february": 2, "march": 3, "may": 5, "june": 6,
          "july": 7, "october": 10, "december": 12}


def fold(s: str) -> str:
    s = (s or "").lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def distinctive_tokens(legal_name: str) -> list[str]:
    n = fold(legal_name)
    n = re.sub(LEGAL_SUFFIX, " ", n)
    n = re.sub(r"[^a-z0-9& ]", " ", n)
    stop = {"og", "the", "of", "i", "&", "holding", "invest", "eiendom"}
    toks = [t for t in n.split() if t not in stop and len(t) > 1]
    return toks or [t for t in n.split() if len(t) > 1]


def site_root(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme.lower()}://{p.hostname}/" if p.hostname else ""


def candidates(entity: dict) -> list[dict]:
    """Ordered website candidates; DNS-checked later. Search engines are not used."""
    out, seen = [], set()
    def push(url, method):
        root = site_root(url)
        host = urllib.parse.urlsplit(root).hostname if root else None
        if host and host not in seen:
            seen.add(host)
            out.append({"url": root, "host": host, "method": method})
    if entity.get("hjemmeside"):
        push(entity["hjemmeside"], "registry_listed")
    email = (entity.get("epostadresse") or "").strip().lower()
    if "@" in email:
        dom = email.split("@", 1)[1]
        if dom not in FREE_MAIL and not dom.endswith(".local"):
            push("https://" + dom, "registry_email_domain")
    toks = distinctive_tokens(entity.get("navn") or "")
    if toks and len("".join(toks)) >= 4 and len(toks) <= 3:
        push("https://" + "".join(toks) + ".no", "name_derived")
        if len(toks) > 1:
            push("https://" + "-".join(toks) + ".no", "name_derived")
    return out


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    retrieved_at: str
    sha256: str
    title: str = ""
    meta_description: str = ""
    og: dict = field(default_factory=dict)
    text: str = ""
    links: list = field(default_factory=list)   # (absolute url, anchor text)
    jsonld: list = field(default_factory=list)
    times: list = field(default_factory=list)   # (datetime attr, context text, href)
    paragraphs: list = field(default_factory=list)


def parse_page(r: Response) -> Page:
    html = r.text()
    soup = BeautifulSoup(html, "html.parser")
    pg = Page(r.url, r.final_url, r.status, r.retrieved_at, r.sha256)
    pg.title = " ".join((soup.title.string or "").split()) if soup.title and soup.title.string else ""
    for m in soup.find_all("meta"):
        k = (m.get("name") or m.get("property") or "").lower()
        if k == "description":
            pg.meta_description = " ".join((m.get("content") or "").split())
        elif k.startswith("og:"):
            pg.og[k] = " ".join((m.get("content") or "").split())
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
        except (ValueError, TypeError):
            continue
        items = d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []
        pg.jsonld.extend(i for i in items if isinstance(i, dict))
    for t in soup.find_all("time"):
        dt = t.get("datetime") or t.get_text(" ", strip=True)
        own = " ".join(t.get_text(" ", strip=True).split())
        title, href, node = "", None, t
        for _ in range(4):  # climb to the enclosing item and take its headline or main link
            node = node.parent
            if node is None or node.name in ("body", "main"):
                break
            cand = [x for x in node.find_all(["h1", "h2", "h3", "h4", "a"]) if len(x.get_text(" ", strip=True)) >= 12
                    and x.get_text(" ", strip=True) != own]
            if cand:
                title = " ".join(cand[0].get_text(" ", strip=True).split())[:200]
                a = cand[0] if cand[0].name == "a" else (cand[0].find("a", href=True) or node.find("a", href=True))
                href = urllib.parse.urljoin(r.final_url, a["href"]) if a is not None and a.get("href") else None
                break
        if title:
            pg.times.append((dt, title, href))
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(r.final_url, a["href"].strip())
        pg.links.append((href, " ".join(a.get_text(" ", strip=True).split())[:120]))
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    pg.paragraphs = [" ".join(p.get_text(" ", strip=True).split()) for p in soup.find_all(["p", "h1", "h2"])]
    pg.text = " ".join(soup.get_text(" ", strip=True).split())
    return pg


def orgnr_on_page(pg: Page, orgnr: str) -> str | None:
    hit = on.mentions(pg.text, orgnr)
    if hit:
        return hit
    for item in pg.jsonld:
        for k in ("taxID", "vatID", "identifier", "leiCode"):
            v = item.get(k)
            if isinstance(v, (str, int)) and on.normalise(v).find(orgnr) >= 0:
                return f'JSON-LD {k}: "{v}"'
    return None


def other_orgnrs(pg: Page, orgnr: str) -> list[str]:
    found = set()
    for m in re.finditer(r"(?i)(org\.?\s*(nr|nummer)\.?|organisasjonsnummer|foretaksregisteret)\D{0,12}((\d[\s.\u00a0]?){9})", pg.text):
        n = on.normalise(m.group(3))
        if on.is_valid(n) and n != orgnr:
            found.add(n)
    return sorted(found)


def names_company(pg: Page, legal_name: str) -> bool:
    toks = distinctive_tokens(legal_name)
    hay = fold(" ".join([pg.title, pg.og.get("og:site_name", ""), pg.text[:20000]]))
    hay = re.sub(r"[^a-z0-9& ]", " ", hay)
    return bool(toks) and all(re.search(rf"\b{re.escape(t)}\b", hay) for t in toks)


def same_site(url: str, host: str) -> bool:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    strip = lambda x: x[4:] if x.startswith("www.") else x
    return strip(h) == strip(host.lower())


def internal_links(pg: Page, host: str, limit=80) -> list[tuple[str, str]]:
    out, seen = [], set()
    for href, text in pg.links:
        if not same_site(href, host) or href.split("#")[0] in seen:
            continue
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|zip|docx?|xlsx?)($|\?)", href, re.I):
            continue
        seen.add(href.split("#")[0])
        out.append((href.split("#")[0], text))
    return out[:limit]


def keyword_link(links, role) -> str | None:
    pat = KEYWORDS[role]
    for href, text in links:
        if pat.search(urllib.parse.urlsplit(href).path) or pat.search(text):
            return href
    return None


def social_profiles(pg: Page) -> list[tuple[str, str, str]]:
    out, seen = [], set()
    def key(u):
        return re.sub(r"^https?://(www\.|m\.|[a-z]{2}\.|nb-no\.)?", "", u.lower()).rstrip("/")
    for href, text in pg.links:
        for platform, pat in SOCIAL:
            m = pat.match(href)
            if m:
                url = m.group(0).rstrip("/")
                if key(url) not in seen:
                    seen.add(key(url))
                    out.append((platform, url, text))
    for item in pg.jsonld:
        same = item.get("sameAs")
        for href in (same if isinstance(same, list) else [same] if isinstance(same, str) else []):
            for platform, pat in SOCIAL:
                m = pat.match(href or "")
                if m and key(m.group(0)) not in seen:
                    seen.add(key(m.group(0)))
                    out.append((platform, m.group(0).rstrip("/"), "JSON-LD sameAs"))
    return out


def contacts(pg: Page) -> dict:
    emails, phones = [], []
    for href, _ in pg.links:
        if href.lower().startswith("mailto:"):
            e = urllib.parse.unquote(href[7:].split("?")[0]).strip().lower()
            if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) and e not in emails:
                emails.append(e)
        elif href.lower().startswith("tel:"):
            t = re.sub(r"[^\d+]", "", href[4:])
            if 8 <= len(t.lstrip("+")) <= 13 and t not in phones:
                phones.append(t)
    return {"emails": emails[:5], "phones": phones[:5]}


def parse_date(s: str) -> str | None:
    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", s)
    if m:
        d, mo, y = map(int, m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"\b(\d{1,2})\.?\s+([A-Za-zæøå]+)\s+(\d{4})\b", s)
    if m and fold(m.group(2)) in {fold(k) for k in MONTHS}:
        mo = {fold(k): v for k, v in MONTHS.items()}[fold(m.group(2))]
        return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"
    return None


def dated_items(pg: Page, limit=10) -> list[dict]:
    items, seen = [], set()
    for it in pg.jsonld:
        t = it.get("@type")
        if (t if isinstance(t, str) else " ".join(t or [])) in ("NewsArticle", "BlogPosting", "Article", "Event", "PressRelease"):
            d = parse_date(str(it.get("datePublished") or it.get("startDate") or ""))
            title = it.get("headline") or it.get("name")
            if d and title and (title, d) not in seen:
                seen.add((title, d))
                items.append({"date": d, "title": str(title)[:160], "url": it.get("url") or pg.final_url, "method": "json_ld"})
    for dt, ctx, href in pg.times:
        d = parse_date(dt)
        if d and ctx and (ctx, d) not in seen:
            seen.add((ctx, d))
            items.append({"date": d, "title": ctx[:160], "url": href or pg.final_url, "method": "html_time_element"})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def job_postings_jsonld(pg: Page) -> list[dict]:
    out = []
    for it in pg.jsonld:
        t = it.get("@type")
        if (t if isinstance(t, str) else " ".join(t or [])) == "JobPosting" and it.get("title"):
            out.append({"title": str(it["title"])[:160], "date_posted": parse_date(str(it.get("datePosted") or "")),
                        "valid_through": parse_date(str(it.get("validThrough") or "")), "url": it.get("url") or pg.final_url})
    return out


def description_candidates(pages: list[Page], limit=40) -> list[str]:
    seen, out = set(), []
    for pg in pages:
        for s in [pg.meta_description, pg.og.get("og:description", "")] + pg.paragraphs:
            s = (s or "").strip()
            w = len(s.split())
            if 6 <= w <= 60 and fold(s) not in seen and not re.search(r"(cookie|informasjonskapsler|javascript|personvern)", s, re.I):
                seen.add(fold(s))
                out.append(s)
    return out[:limit]


class Robots:
    def __init__(self, f: Fetcher):
        self.f = f
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool | None:
        """True/False per robots.txt; None when the host cannot be reached at all (then no page fetch is attempted)."""
        from . import USER_AGENT
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.hostname}"
        if root not in self.cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.f.get(root + "/robots.txt", accept="text/plain")
                if r.status in (401, 403):
                    rp.disallow_all = True
                elif r.status == 200 and "html" not in r.content_type.lower():
                    rp.parse(r.text().splitlines())
                else:
                    rp.allow_all = True
            except UnsafeURL:
                rp = None
            except (OSError, ValueError):
                rp = None
            self.cache[root] = rp
        rp = self.cache[root]
        return None if rp is None else rp.can_fetch(USER_AGENT.split("/")[0], url)


def fetch_page(f: Fetcher, robots: Robots, url: str) -> tuple[Page | None, str | None]:
    """Returns (page, error_state). error_state is 'blocked' (robots) or 'failed'."""
    try:
        ok = robots.allowed(url)
        if ok is None:
            return None, "failed: host unreachable"
        if not ok:
            return None, "blocked"
        r = f.get(url, accept="text/html,application/xhtml+xml")
    except BudgetExhausted:
        raise
    except (UnsafeURL, OSError, ValueError) as e:
        return None, f"failed: {type(e).__name__}: {str(e)[:120]}"
    if r.status != 200 or "html" not in r.content_type.lower():
        return None, f"failed: HTTP {r.status} {r.content_type[:40]}"
    return parse_page(r), None


def candidate_resolves(f, c: dict) -> bool:
    return f.resolves(c["host"]) or (not c["host"].startswith("www.") and f.resolves("www." + c["host"]))
