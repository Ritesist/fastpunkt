"""Decision-useful synthesis written by code from published claims only. Every sentence lists the claim ids
it rests on, numbers are computed in code (never by a model), and unknowns are stated explicitly."""
from __future__ import annotations

from .claims import ClaimSet


def _fmt_money(v: dict | None) -> str | None:
    if not v:
        return None
    a, cur = v["amount"], v.get("currency") or ""
    sign = "-" if a < 0 else ""
    a = abs(a)
    if a >= 1e9:
        s = f"{a / 1e9:.1f} billion"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f} million"
    elif a >= 1e3:
        s = f"{a / 1e3:.0f} thousand"
    else:
        s = f"{a:.0f}"
    return f"{sign}{cur} {s}".strip()


def derive_signals(cs: ClaimSet, series: list[dict]) -> list[dict]:
    """Year-over-year changes from the filed company accounts (same currency only)."""
    out = []
    comp = [y for y in series if y["type"] == "selskap"]
    if len(comp) >= 2:
        a, b = comp[-2], comp[-1]
        for k in ("revenue", "operating_result", "net_result", "equity"):
            if a.get(k) is not None and b.get(k) is not None and a["currency"] == b["currency"]:
                d = b[k] - a[k]
                pct = (d / abs(a[k]) * 100) if a[k] else None
                ids = [c["id"] for c in cs.claims if c["field"] == k and c.get("reporting_period") in (a["period"], b["period"])]
                out.append({"signal": f"{k}_change", "from_period": a["period"], "to_period": b["period"], "currency": b["currency"],
                            "before": a[k], "after": b[k], "change": d, "change_pct": None if pct is None else round(pct, 1),
                            "claim_ids": ids, "method": "computed in code from filed accounts"})
    if comp:
        last = comp[-1]
        if last.get("equity") is not None and last["equity"] < 0:
            ids = [c["id"] for c in cs.claims if c["field"] == "equity" and c.get("reporting_period") == last["period"]]
            out.append({"signal": "negative_equity", "period": last["period"], "value": last["equity"], "claim_ids": ids,
                        "method": "computed in code from filed accounts"})
    return out


def summarise(cs: ClaimSet, sec: dict, signals: list[dict]) -> dict:
    S = []
    def cid(field):
        return [c["id"] for c in cs.get(field)]
    def add(text, ids):
        if text:
            S.append({"text": text, "claim_ids": ids})

    name, form, muni = cs.first("legal_name"), cs.first("legal_form"), cs.first("municipality")
    if name:
        kind = form.split(" (", 1)[1].rstrip(")").lower() if form and " (" in form else "registered entity"
        code = form.split(" (", 1)[0] if form else ""
        art = "an" if kind[:1] in "aeiouyæøå" else "a"
        bits = [f"{name} is {art} {kind}" + (f" ({code})" if code else "")]
        if muni:
            bits.append(f"based in {muni.title()}")
        ind = cs.first("industry")
        if ind:
            bits.append(f"registered under industry code {ind}")
        add(", ".join(bits) + ".", cid("legal_name") + cid("legal_form") + cid("municipality") + cid("industry"))
    desc = cs.first("business_description")
    if desc:
        add(f"Its website describes it as: \u201c{desc}\u201d", cid("business_description"))
    flags = [f for f, label in (("bankrupt", "bankrupt"), ("under_liquidation", "under liquidation"),
                                ("under_forced_dissolution", "under forced dissolution")) if cs.first(f) is True]
    if flags:
        add("The register marks it as " + ", ".join(flags).replace("_", " ") + ".", sum((cid(f) for f in flags), []))
    emp = cs.first("registered_employees")
    if emp is not None:
        add(f"It has {emp} registered employees.", cid("registered_employees"))
    rev = [c for c in cs.get("revenue")]
    if rev:
        last = max(rev, key=lambda c: c.get("reporting_period", ""))
        per = last["reporting_period"].split("/")[1][:4]
        parts = [f"revenue {_fmt_money(last['value'])}"]
        ids = [last["id"]]
        for k, label in (("operating_result", "operating result"), ("net_result", "net result"), ("equity", "equity")):
            m = [c for c in cs.get(k) if c.get("reporting_period") == last["reporting_period"]]
            if m:
                parts.append(f"{label} {_fmt_money(m[0]['value'])}")
                ids.append(m[0]["id"])
        add(f"Filed company accounts for {per}: " + ", ".join(parts) + ".", ids)
    for s in signals:
        if s["signal"] == "revenue_change" and s["change_pct"] is not None:
            add(f"Revenue changed by {s['change_pct']:+.1f}% from {s['from_period'][:4]} to {s['to_period'][:4]} (computed from the filed accounts).", s["claim_ids"])
        if s["signal"] == "negative_equity":
            add(f"Equity was negative at the end of {s['period'][-10:-6]}.", s["claim_ids"])
    ceo, chair = cs.first("ceo"), cs.first("board_chair")
    lead = [x for x in [f"general manager {ceo}" if ceo else None, f"board chair {chair}" if chair else None] if x]
    if lead:
        add("Leadership in the roles register: " + " and ".join(lead) + ".", cid("ceo") + cid("board_chair"))
    wc = cs.first("registered_workplace_count")
    if wc:
        add(f"It has {wc} registered workplace{'s' if wc != 1 else ''} (subunits).", cid("registered_workplace_count"))
    site = cs.first("official_website")
    if site:
        how = cs.get("official_website")[0].get("verification")
        why = {"orgnr_on_site": "the site shows its organisation number", "registry_listed": "it is the website listed in the register",
               "name_match_and_typesafe_identity": "the site names the company and an identity check confirmed it"}.get(how, how)
        soc = sorted({c["value"]["platform"] for c in cs.get("social_profile")})
        rel = cs.get("official_website")[0].get("relationship")
        if rel:
            add(f"Website listed for it: {site} ({why}); note: {rel}, so the site's content is not attributed to this company.",
                cid("official_website"))
        else:
            add(f"Official website: {site} (verified because {why})" + (f"; it links to company profiles on {', '.join(soc)}." if soc else "."),
                cid("official_website") + cid("social_profile"))
    jc = cs.get("job_postings_count")
    if jc:
        n = jc[0]["value"]
        add(f"Hiring: the careers page lists {n} specific open position{'s' if n != 1 else ''}." if n else
            "Hiring: a careers page exists, but no specific open position was identified when checked.", cid("job_postings_count"))
    acts = cs.get("dated_activity")
    if acts:
        latest = max(acts, key=lambda c: c["value"]["date"])
        add(f"Most recent dated item on the company site: {latest['value']['date']}, \u201c{latest['value']['title'][:90]}\u201d.", [latest["id"]])
    unknowns = []
    for k, v in sec.items():
        if v["state"] != "available":
            unknowns.append({"section": k, "state": v["state"], "reason": v.get("reason", "")})
    for c in cs.claims:
        if c["availability"] != "available" and c["field"] in ("ceo", "board_chair", "registered_employees", "official_website", "careers_page"):
            unknowns.append({"field": c["field"], "state": c["availability"], "reason": c.get("reason", "")})
    return {"sentences": S, "text": " ".join(s["text"] for s in S), "unknowns": unknowns,
            "method": "template synthesis from published claims; no generated text"}
