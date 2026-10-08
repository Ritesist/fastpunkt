"""Research one company: official records first (identity anchor), then the verified company website."""
from __future__ import annotations

import time
import urllib.parse

from . import brreg, judge as J, website as W
from .claims import ClaimSet
from .fetch import BudgetExhausted, Fetcher, utcnow
from .summary import derive_signals, summarise

SECTIONS = ["legal_identity", "annual_accounts", "leadership_workplaces", "website_profiles", "hiring_activity"]


class CompanyBudget:
    """Per-company request cap on top of the global budget, so one large site cannot starve the batch."""
    def __init__(self, f: Fetcher, cap: int):
        self.f, self.cap, self.used = f, cap, 0

    def get(self, url, **kw):
        if self.used >= self.cap:
            raise BudgetExhausted(f"per-company request cap ({self.cap}) reached")
        self.used += 1
        return self.f.get(url, **kw)

    def __getattr__(self, k):
        return getattr(self.f, k)


def research(orgnr: str, entity_hit, f: Fetcher, judge: J.Judge, cap: int, run_id: str, deep: bool = True) -> dict:
    t0 = time.time()
    started = utcnow()
    cs = ClaimSet()
    sec = {s: {"state": "failed", "reason": "not attempted"} for s in SECTIONS}
    errors, candidates_out = [], []
    cf = CompanyBudget(f, cap)
    jcalls0 = judge.input_tokens

    def fail(section, e):
        state = "failed"
        sec[section] = {"state": state, "reason": f"{type(e).__name__}: {str(e)[:160]}"}
        errors.append({"section": section, "error": type(e).__name__, "message": str(e)[:300]})

    # 1. Legal identity (anchor). Batch hit from the runner, or a single lookup.
    entity = None
    try:
        if entity_hit:
            entity, r_ent = entity_hit
        else:
            entity, r_ent = brreg.fetch_entity(cf, orgnr)
        if entity is None:
            sec["legal_identity"] = {"state": "not_available", "reason": f"Enhetsregisteret has no entity {orgnr} (HTTP {r_ent.status})."}
        elif entity.get("_deleted"):
            sec["legal_identity"] = {"state": "available", "reason": f"Entity deleted from the register on {entity.get('slettedato')}."}
            cs.add("legal_identity", "deleted_date", entity.get("slettedato"),
                   [brreg._ev(cs, r_ent, f"{brreg.ENHET}/{orgnr}", f"slettedato: {entity.get('slettedato')}")])
            entity = None
        else:
            brreg.identity_claims(cs, entity, r_ent)
            sec["legal_identity"] = {"state": "available"}
    except Exception as e:  # noqa: BLE001 - every input must still end in an envelope
        fail("legal_identity", e)

    series = []
    if entity:
        # 2. Annual accounts with history
        try:
            rows, r = brreg.fetch_accounts(cf, orgnr)
            series = brreg.accounts_claims(cs, orgnr, rows, r)
            sec["annual_accounts"] = {"state": "available" if rows else "not_available",
                                      **({} if rows else {"reason": "No filed accounts returned by Regnskapsregisteret."})}
        except Exception as e:  # noqa: BLE001
            fail("annual_accounts", e)
        # 3. Leadership and registered workplaces
        try:
            data, r = brreg.fetch_roles(cf, orgnr)
            brreg.roles_claims(cs, orgnr, data, r)
            sec["leadership_workplaces"] = {"state": "available" if data else "not_available"}
        except Exception as e:  # noqa: BLE001
            fail("leadership_workplaces", e)
        # 4-5. Website, company-owned profiles, hiring and dated activity
        try:
            site_research(cs, sec, entity, orgnr, cf, judge, candidates_out, deep)
        except BudgetExhausted as e:
            for s in ("website_profiles", "hiring_activity"):
                if sec[s]["state"] == "failed" and sec[s]["reason"] == "not attempted":
                    sec[s] = {"state": "failed", "reason": f"BudgetExhausted: {e}"}
        except Exception as e:  # noqa: BLE001
            fail("website_profiles", e)
        if deep:
            try:
                data, r = brreg.fetch_subunits(cf, orgnr)
                brreg.subunit_claims(cs, orgnr, data, r)
            except Exception as e:  # noqa: BLE001
                errors.append({"section": "leadership_workplaces", "error": type(e).__name__, "message": str(e)[:300]})
    else:
        for s in SECTIONS[1:]:
            if sec["legal_identity"]["state"] != "available":
                sec[s] = {"state": "not_applicable", "reason": "No registry identity to anchor research."}
            elif sec[s]["reason"] == "not attempted":
                sec[s] = {"state": "not_applicable", "reason": "Entity is deleted from the register."}

    signals = derive_signals(cs, series)
    summary = summarise(cs, sec, signals)
    tokens = judge.input_tokens - jcalls0
    return {
        "organisation_number": orgnr,
        "run": {"run_id": run_id, "started_at": started, "completed_at": utcnow(), "terminal_status": "completed"},
        "legal_identity": {k: cs.first(k) for k in ("legal_name", "legal_form", "business_address", "industry", "registered_employees")},
        "sections": sec,
        "summary": summary,
        "signals": signals,
        "claims": cs.claims,
        "evidence": cs.evidence,
        "website_candidates": candidates_out,
        "changes": [],
        "errors": errors,
        "operations": {"requests": cf.used, "runtime_ms": int((time.time() - t0) * 1000),
                       "typesafe_input_tokens": tokens,
                       "third_party_cost_usd": round(tokens * J.PRICE_PER_INPUT_TOKEN, 6)},
    }


def _registry_view(e: dict) -> dict:
    fa = e.get("forretningsadresse") or {}
    return {"legal_name": e.get("navn"), "organisation_number": e.get("organisasjonsnummer"),
            "business_address": brreg._addr(fa), "municipality": fa.get("kommune"),
            "industry": (e.get("naeringskode1") or {}).get("beskrivelse"), "registry_email": e.get("epostadresse"),
            "registered_activity": " ".join(e.get("aktivitet") or [])[:400]}


def _site_ev(cs: ClaimSet, pg: W.Page, span: str, method: str) -> str:
    return cs.add_evidence(source_url=pg.final_url, source_class="company_owned", source_name=urllib.parse.urlsplit(pg.final_url).hostname,
                           retrieved_at=pg.retrieved_at, content_sha256=pg.sha256, claim_span=span[:300], extraction_method=method)


def site_research(cs, sec, entity, orgnr, cf, judge, candidates_out, deep):
    S, H = "website_profiles", "hiring_activity"
    robots = W.Robots(cf)
    cands = W.candidates(entity)
    verified = None
    blocked_any = False
    for c in cands[:3]:
        rec = {"url": c["url"], "discovery": c["method"]}
        candidates_out.append(rec)
        if c["method"] != "registry_listed" and not W.candidate_resolves(cf, c):
            rec.update(status="rejected", reason="domain does not resolve (DNS only, no request made)")
            continue
        pg, err = W.fetch_page(cf, robots, c["url"])
        if pg is None and err != "failed: host unreachable" and not c["host"].startswith("www."):
            pg, err = W.fetch_page(cf, robots, c["url"].replace("://", "://www.", 1))
        if pg is None:
            blocked_any |= (err == "blocked")
            rec.update(status="blocked" if err == "blocked" else "unreachable", reason=err)
            continue
        hit = W.orgnr_on_page(pg, orgnr)
        hit_page = pg
        others = W.other_orgnrs(pg, orgnr)
        links = W.internal_links(pg, urllib.parse.urlsplit(pg.final_url).hostname)
        pages = [pg]
        # Contact/about pages often carry the org number in the footer or company details.
        if not hit and c["method"] != "registry_listed":
            for role in ("contact", "about"):
                u = W.keyword_link(links, role)
                if u and not hit:
                    p2, _ = W.fetch_page(cf, robots, u)
                    if p2:
                        pages.append(p2)
                        h2 = W.orgnr_on_page(p2, orgnr)
                        if h2:
                            hit, hit_page = h2, p2
        method, conf, ident_p = None, None, None
        if hit:
            method, conf = "orgnr_on_site", 0.99
        elif c["method"] == "registry_listed":
            method, conf = "registry_listed", 0.95
        names = W.names_company(pg, entity.get("navn") or "")
        sentences = W.description_candidates(pages)
        site_view = {"url": pg.final_url, "title": pg.title, "site_name": pg.og.get("og:site_name", ""),
                     "meta_description": pg.meta_description, "other_org_numbers_on_page": others,
                     "text_excerpt": pg.text[:3500]}
        state, q = J.site_questions(_registry_view(entity), site_view, links[:60], sentences[:30], need_identity=method is None)
        ans = (judge.ask(state, q) or {}).get("answers", {}) if judge.enabled else {}
        if method is None:
            ident_p = (ans.get("identity") or {}).get("noul")
            if names and not others and ident_p is not None and ident_p >= J.IDENTITY_THRESHOLD:
                method, conf = "name_match_and_typesafe_identity", round(ident_p, 3)
        rec.update(final_url=pg.final_url, orgnr_on_site=bool(hit), names_company=names, other_org_numbers=others,
                   typesafe_identity_p=ident_p)
        if method is None:
            rec.update(status="ambiguous", reason="no organisation number on the site and identity not confirmed; not published")
            continue
        if method == "registry_listed" and others and not hit:
            rec["relationship_note"] = f"site shows other organisation number(s) {others}: may be a group or brand site"
            rec["shared_site"] = True
        elif method == "registry_listed" and not hit and not names:
            rec["relationship_note"] = ("the registry lists this site, but it shows neither this company's name nor its "
                                        "organisation number: may be a brand, franchise or group site")
            rec["shared_site"] = True
        rec.update(status="verified", verification=method)
        verified = (pg, pages, links, ans, method, conf, hit, rec, hit_page)
        break

    if not verified:
        if any(r.get("status") == "ambiguous" for r in candidates_out):
            sec[S] = {"state": "ambiguous", "reason": "A candidate site was found but could not be tied to this exact entity."}
        elif blocked_any:
            sec[S] = {"state": "blocked", "reason": "Candidate site disallows automated access (robots.txt)."}
        elif any(r.get("status") == "unreachable" and r.get("discovery") != "name_derived" for r in candidates_out):
            # A site the company itself points to (registry entry or e-mail domain) did not answer.
            sec[S] = {"state": "failed", "reason": "A registry-listed or e-mail-domain site could not be fetched."}
        elif any(r.get("status") == "unreachable" for r in candidates_out):
            # Only guessed domains, none answering: absence, not a failure of a known source.
            sec[S] = {"state": "not_available", "reason": "No registry website or company e-mail domain; guessed .no domains did not answer."}
        else:
            sec[S] = {"state": "not_available", "reason": "No registry website, no company e-mail domain and no resolving name-derived domain."}
        cs.missing(S, "official_website", sec[S]["state"], sec[S]["reason"])
        sec[H] = {"state": "not_available", "reason": "No verified company website to check for jobs or dated activity."}
        return

    pg, pages, links, ans, method, conf, hit, rec, hit_page = verified
    span = hit or (f"registry hjemmeside: {entity.get('hjemmeside')}" if method == "registry_listed" else pg.title)
    ev = [_site_ev(cs, hit_page if hit else pg, span, method)]
    if method == "registry_listed":
        ev.append(cs.get("registry_website")[0]["evidence_ids"][0]) if cs.get("registry_website") else None
    cs.add(S, "official_website", pg.final_url, ev, confidence=conf, verification=method,
           relationship=rec.get("relationship_note"))
    sec[S] = {"state": "available", "verification": method}
    if pg.title:
        cs.add(S, "website_title", pg.title, [_site_ev(cs, pg, pg.title, "html_title")], confidence=conf)
    if rec.get("shared_site"):
        # Brand/franchise/group site: the link itself is the registry's statement, but the site's own
        # description, contacts, profiles and jobs may belong to another legal entity. Label, don't collapse.
        sec[S]["relationship"] = rec["relationship_note"]
        reason = "Site may belong to a brand, franchise or group, so its content is not attributed to this legal entity."
        for fld in ("business_description", "social_profile", "website_email", "website_phone"):
            cs.missing(S, fld, "ambiguous", reason)
        sec[H] = {"state": "ambiguous", "reason": reason}
        return
    # Self-description: Jev selects a sentence; the text is copied verbatim from the page.
    sentences = W.description_candidates(pages)[:30]
    d = ans.get("description") or {}
    if d.get("choice", "none") != "none" and d.get("choice", "").startswith("S"):
        i = int(d["choice"][1:])
        if i < len(sentences):
            cs.add(S, "business_description", sentences[i], [_site_ev(cs, pg, sentences[i], "typesafe_choice_verbatim")],
                   confidence=round(d.get("confidence", 0), 3), note="company's own wording")
    elif pg.meta_description:
        cs.add(S, "business_description", pg.meta_description, [_site_ev(cs, pg, pg.meta_description, "meta_description")],
               confidence=conf, note="company's own wording")
    for platform, url, text in W.social_profiles(pg):
        cs.add(S, "social_profile", {"platform": platform, "url": url},
               [_site_ev(cs, pg, f'link "{text}" -> {url}', "outbound_link_on_verified_site")], confidence=conf,
               note="linked from the verified company site; profile content not collected")
    ct = W.contacts(pg)
    for e in ct["emails"]:
        cs.add(S, "website_email", e, [_site_ev(cs, pg, f"mailto:{e}", "mailto_link")], confidence=conf)
    for t in ct["phones"]:
        cs.add(S, "website_phone", t, [_site_ev(cs, pg, f"tel:{t}", "tel_link")], confidence=conf)

    # Hiring and dated public activity
    def pick(role):
        a = ans.get(f"link_{role}") or {}
        ch = a.get("choice", "none")
        if ch.startswith("L") and int(ch[1:]) < len(links[:60]) and a.get("confidence", 0) >= 0.5:
            return links[int(ch[1:])][0], "typesafe_choice"
        u = W.keyword_link(links, role)
        return (u, "keyword") if u else (None, None)

    host = urllib.parse.urlsplit(pg.final_url).hostname
    ats_links = [(h, t) for h, t in pg.links if W.ATS.search(h)]
    careers_url, careers_how = pick("careers")
    news_url, news_how = pick("news")
    checked_careers = False
    jobs = W.job_postings_jsonld(pg)
    items = [(it, pg) for it in W.dated_items(pg)]
    robots = W.Robots(cf) if deep else None
    if deep and careers_url:
        cp, err = W.fetch_page(cf, robots, careers_url)
        if cp:
            checked_careers = True
            cs.add(H, "careers_page", cp.final_url, [_site_ev(cs, cp, cp.title or careers_url, f"careers_link_{careers_how}")], confidence=conf)
            jobs += W.job_postings_jsonld(cp)
            ats_links += [(h, t) for h, t in cp.links if W.ATS.search(h)]
            if not jobs:
                cand = [(h, t) for h, t in cp.links if t and 1 <= len(t.split()) <= 9 and (W.same_site(h, host) or W.ATS.search(h))
                        and h.rstrip("/") != cp.final_url.rstrip("/") and not W.KEYWORDS["contact"].search(t)][:30]
                st, q = J.job_questions(entity.get("navn"), cand)
                ja = (judge.ask(st, q) or {}).get("answers", {}) if cand and judge.enabled else {}
                for i, (h, t) in enumerate(cand):
                    p = (ja.get(f"job_{i}") or {}).get("noul")
                    if p is not None and p >= 0.8:
                        jobs.append({"title": t, "url": h, "date_posted": None, "valid_through": None, "typesafe_p": round(p, 3)})
            for j in jobs[:25]:
                cs.add(H, "job_posting", j, [_site_ev(cs, cp, f"{j['title']} -> {j.get('url')}", "json_ld_jobposting" if "typesafe_p" not in j else "typesafe_noul_link")],
                       confidence=j.get("typesafe_p", conf), effective_date=j.get("date_posted"))
            cs.add(H, "job_postings_count", len(jobs), [_site_ev(cs, cp, f"{len(jobs)} open positions identified on {cp.final_url}", "count_on_careers_page")],
                   confidence=conf, note="0 means the careers page was checked and no specific open position was identified")
        else:
            errors_note = err
            cs.missing(H, "job_postings_count", "blocked" if err == "blocked" else "failed", f"Careers page not fetched: {errors_note}")
    elif deep:
        cs.missing(H, "careers_page", "not_available", "No careers page linked from the verified homepage.")
    for h, t in ats_links[:5]:
        cs.add(H, "recruiting_system_link", h, [_site_ev(cs, pg, f'link "{t}" -> {h}', "outbound_link_on_verified_site")], confidence=conf)
    if deep and news_url and news_url != careers_url:
        npg, err = W.fetch_page(cf, robots, news_url)
        if npg:
            cs.add(H, "news_page", npg.final_url, [_site_ev(cs, npg, npg.title or news_url, f"news_link_{news_how}")], confidence=conf)
            items = [(it, npg) for it in W.dated_items(npg)] + items
    seen = set()
    for it, src in items:
        k = (it["title"], it["date"])
        if k in seen:
            continue
        seen.add(k)
        cs.add(H, "dated_activity", it, [_site_ev(cs, src, f"{it['date']}: {it['title']}", it["method"])], confidence=conf,
               effective_date=it["date"])
        if len(seen) >= 10:
            break
    any_h = checked_careers or seen or ats_links or jobs
    sec[H] = {"state": "available"} if any_h else {"state": "not_available", "reason": "Verified site has no careers page, job postings or dated items we could read."}
