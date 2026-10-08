"""TypeSafe Jev judgments ("select, don't generate"). Code finds every candidate (links, sentences, job titles);
Jev only chooses among them or returns a calibrated yes/no probability. Nothing Jev returns is free text,
so it cannot invent a fact; published values are always copied from the page.

Requires TYPESAFE_API_KEY in the environment (server-side only). Without it, the agent falls back to keyword
rules and never publishes a website that lacks hard identity evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("FASTPUNKT_TYPESAFE_MODEL", "jev-1.13.0")  # pinned: thresholds were set on this version
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # USD; output tokens are free (docs.typesafe.ai/models)
IDENTITY_THRESHOLD = 0.9


class Judge:
    def __init__(self, api_key: str | None = None, max_calls: int = 5000, opener=None, record_dir=None, replay_dir=None):
        self.key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY")
        self.record_dir, self.replay_dir = record_dir, replay_dir
        self.enabled = bool(self.key) or bool(replay_dir)
        self.max_calls = max_calls
        self.calls = 0
        self.input_tokens = 0
        self.errors = 0
        self._lock = threading.Lock()
        self._open = opener or urllib.request.urlopen

    @property
    def cost_usd(self) -> float:
        return round(self.input_tokens * PRICE_PER_INPUT_TOKEN, 6)

    def ask(self, state, questions: dict) -> dict | None:
        if not self.enabled or not questions:
            return None
        with self._lock:
            if self.calls >= self.max_calls:
                return None
            self.calls += 1
        body = json.dumps({"state": state, "model": MODEL, "questions": questions}, sort_keys=True).encode()
        key = hashlib.sha256(body).hexdigest()[:24]
        if self.replay_dir:
            f = Path(self.replay_dir) / f"typesafe-{key}.json"
            if not f.exists():
                return None
            d = json.loads(f.read_text())
            with self._lock:
                self.input_tokens += (d.get("usage") or {}).get("input_tokens", 0)
            return d
        for attempt in range(4):
            req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                         headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            try:
                with self._open(req, timeout=30) as r:
                    d = json.loads(r.read())
                with self._lock:
                    self.input_tokens += (d.get("usage") or {}).get("input_tokens", 0)
                if self.record_dir:
                    Path(self.record_dir).mkdir(parents=True, exist_ok=True)
                    (Path(self.record_dir) / f"typesafe-{key}.json").write_text(json.dumps(
                        {"model": d.get("model"), "usage": d.get("usage"), "answers": d.get("answers")}))
                return d
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    time.sleep(0.8 * 2 ** attempt)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ValueError):
                if attempt < 3:
                    time.sleep(0.8 * 2 ** attempt)
                    continue
                break
        with self._lock:
            self.errors += 1
        return None


def site_questions(registry: dict, site: dict, links: list, sentences: list, need_identity: bool):
    """One request, several independent questions over the same state (speculative fan-out)."""
    state = {"registry_record": registry, "website": site,
             "links": [{"id": f"L{i}", "text": t, "path": h} for i, (h, t) in enumerate(links)],
             "sentences": [{"id": f"S{i}", "text": s} for i, s in enumerate(sentences)]}
    q = {}
    if need_identity:
        q["identity"] = {
            "type": "noul",
            "instructions": ("Is the website in `website` operated by the exact legal entity in `registry_record` (same company, "
                             "not a parent, subsidiary or sister company, not an accountant, web agency or landlord, not a "
                             "franchise brand of a different entity and not an unrelated business with a similar name)? "
                             "The site text may be Norwegian."),
            "criteria": {"true": "The site clearly presents itself as this company: matching name and consistent location or activity",
                         "false": "The site belongs to a different or only related entity, or the evidence is too weak to tell"},
        }
    roles = {
        "careers": "the careers or job vacancies page (karriere, ledige stillinger, jobb hos oss)",
        "news": "the news, press or blog listing page (nyheter, aktuelt, presse)",
        "about": "the about-us page describing the company (om oss)",
        "contact": "the contact page with address or company details (kontakt)",
    }
    if links:
        for role, desc in roles.items():
            crit = {f"L{i}": f'link "{t}" to {h}' for i, (h, t) in enumerate(links)}
            crit["none"] = f"None of the links is {desc}"
            q[f"link_{role}"] = {"type": "choice", "instructions": f"Which link in `links` goes to {desc} of this website?", "criteria": crit}
    if sentences:
        crit = {f"S{i}": s for i, s in enumerate(sentences)}
        crit["none"] = "No sentence describes what the company does or offers"
        q["description"] = {"type": "choice", "criteria": crit, "instructions": (
            "Which sentence in `sentences` best describes what this company does or offers (its products, services, customers "
            "or activity)? Prefer a factual description over a slogan, and never pick cookie, privacy or navigation text.")}
    return state, q


def job_questions(company: str, candidates: list):
    state = {"company": company, "candidates": [{"id": f"J{i}", "text": t, "url": h} for i, (h, t) in enumerate(candidates)]}
    q = {f"job_{i}": {"type": "noul",
                      "instructions": f"Is candidate J{i} in `candidates` a link to one specific open job position at {company} "
                                      "(a role title such as 'Elektriker' or 'Prosjektleder'), rather than navigation, an open/general "
                                      "application, a news article or a page about working there in general?",
                      "criteria": {"true": "A specific vacant position", "false": "Navigation, general application, article or other"}}
         for i in range(len(candidates))}
    return state, q
