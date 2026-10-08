"""Official Norwegian records from Brønnøysundregistrene (open data, NLOD 2.0).

Endpoints: Enhetsregisteret entities (batched up to 100 per request), roles, subunits (registered workplaces)
and Regnskapsregisteret annual accounts (latest filed years, company and group accounts)."""
from __future__ import annotations

from .claims import ClaimSet
from .fetch import Fetcher, Response

ENHET = "https://data.brreg.no/enhetsregisteret/api/enheter"
UNDERENHET = "https://data.brreg.no/enhetsregisteret/api/underenheter"
REGNSKAP = "https://data.brreg.no/regnskapsregisteret/regnskap"
LICENCE = "NLOD 2.0 (Norwegian Licence for Open Government Data)"
REGISTRY = "Brønnøysundregistrene, Enhetsregisteret"
ACCOUNTS = "Brønnøysundregistrene, Regnskapsregisteret"

# Role codes worth publishing (people are reported by name and role only; birth dates are never stored).
ROLE_FIELDS = {"DAGL": "ceo", "LEDE": "board_chair", "NEST": "board_deputy_chair", "MEDL": "board_member",
               "INNH": "owner_sole_proprietor", "DTPR": "general_partner", "DTSO": "general_partner",
               "KOMP": "general_partner", "REVI": "auditor", "REGN": "accountant", "KONT": "contact_person",
               "FFØR": "business_manager", "SAM": "joint_owner"}


def fetch_entities(f: Fetcher, orgnrs: list[str]) -> dict[str, tuple[dict, Response]]:
    out = {}
    for i in range(0, len(orgnrs), 100):
        chunk = orgnrs[i:i + 100]
        r = f.get(f"{ENHET}?organisasjonsnummer={','.join(chunk)}&size=100", accept="application/json")
        if r.status != 200:
            continue
        for e in (r.json().get("_embedded") or {}).get("enheter", []):
            out[e["organisasjonsnummer"]] = (e, r)
    return out


def fetch_entity(f: Fetcher, orgnr: str):
    r = f.get(f"{ENHET}/{orgnr}", accept="application/json")
    if r.status == 200:
        return r.json(), r
    if r.status == 410:  # deleted entity: the registry still answers with a tombstone
        return {"organisasjonsnummer": orgnr, "slettedato": r.json().get("slettedato"), "_deleted": True}, r
    return None, r


def _ev(cs: ClaimSet, r: Response, url: str, span: str, name=REGISTRY, period=None):
    """`source_url` is the human-checkable record URL; `fetched_url` is the exact request whose bytes were hashed
    (they differ when entities were fetched in a batch of up to 100)."""
    eid = cs.add_evidence(source_url=url, source_class="official_registry", source_name=name, retrieved_at=r.retrieved_at,
                          content_sha256=r.sha256, claim_span=span, extraction_method="json_field", licence=LICENCE,
                          reporting_period=period)
    if r.url != url:
        cs.evidence[int(eid.split("-")[1]) - 1]["fetched_url"] = r.url
    return eid


def _addr(a: dict | None) -> str | None:
    if not a:
        return None
    parts = [", ".join(a.get("adresse") or []), " ".join(x for x in [a.get("postnummer"), a.get("poststed")] if x)]
    if a.get("landkode") and a.get("landkode") != "NO":
        parts.append(a.get("land"))
    s = ", ".join(p for p in parts if p)
    return s or None


def identity_claims(cs: ClaimSet, e: dict, r: Response):
    S = "legal_identity"
    url = f"{ENHET}/{e['organisasjonsnummer']}"
    def add(field, value, key, **kw):
        if value in (None, "", []):
            return None
        return cs.add(S, field, value, [_ev(cs, r, url, f'{key}: {value}')], **kw)
    add("organisation_number", e["organisasjonsnummer"], "organisasjonsnummer")
    add("legal_name", e.get("navn"), "navn")
    lf = e.get("organisasjonsform") or {}
    add("legal_form", f"{lf.get('kode')} ({lf.get('beskrivelse')})" if lf else None, "organisasjonsform")
    add("registered_in_business_register", e.get("registrertIForetaksregisteret"), "registrertIForetaksregisteret")
    add("registration_date", e.get("registreringsdatoEnhetsregisteret"), "registreringsdatoEnhetsregisteret")
    add("founded_date", e.get("stiftelsesdato"), "stiftelsesdato")
    add("business_address", _addr(e.get("forretningsadresse")), "forretningsadresse")
    add("postal_address", _addr(e.get("postadresse")), "postadresse")
    fa = e.get("forretningsadresse") or {}
    add("municipality", fa.get("kommune"), "forretningsadresse.kommune")
    n1 = e.get("naeringskode1")
    add("industry", f"{n1['kode']} {n1['beskrivelse']}" if n1 else None, "naeringskode1")
    if e.get("harRegistrertAntallAnsatte") and e.get("antallAnsatte") is not None:
        add("registered_employees", e["antallAnsatte"], "antallAnsatte",
            effective_date=e.get("registreringsdatoAntallAnsatteNAVAaregisteret") or e.get("registreringsdatoAntallAnsatteEnhetsregisteret"))
    else:
        cs.missing(S, "registered_employees", "not_available", "The registry has no registered employee count for this entity.")
    add("vat_registered", e.get("registrertIMvaregisteret"), "registrertIMvaregisteret")
    add("bankrupt", e.get("konkurs"), "konkurs")
    add("under_liquidation", e.get("underAvvikling"), "underAvvikling")
    add("under_forced_dissolution", e.get("underTvangsavviklingEllerTvangsopplosning"), "underTvangsavviklingEllerTvangsopplosning")
    add("part_of_group", e.get("erIKonsern"), "erIKonsern")
    add("parent_entity", e.get("overordnetEnhet"), "overordnetEnhet", relationship="parent (registry 'overordnetEnhet')")
    if e.get("aktivitet"):
        add("registered_activity", " ".join(e["aktivitet"]), "aktivitet")
    elif e.get("vedtektsfestetFormaal"):
        add("registered_activity", " ".join(e["vedtektsfestetFormaal"]), "vedtektsfestetFormaal")
    k = e.get("kapital") or {}
    if k.get("belop") is not None:
        add("share_capital", {"amount": k["belop"], "currency": k.get("valuta"), "type": k.get("type")}, "kapital.belop",
            effective_date=k.get("innfortDato"))
    for hn in e.get("historiskeNavn") or []:
        add("former_name", {"name": hn.get("navn"), "from": hn.get("fraDato"), "to": hn.get("tilDato")}, "historiskeNavn")
    add("registry_website", e.get("hjemmeside"), "hjemmeside")
    add("registry_email", e.get("epostadresse"), "epostadresse")
    add("registry_phone", e.get("telefon") or e.get("mobil"), "telefon" if e.get("telefon") else "mobil")
    add("latest_submitted_accounts_year", e.get("sisteInnsendteAarsregnskap"), "sisteInnsendteAarsregnskap")


def fetch_accounts(f: Fetcher, orgnr: str):
    r = f.get(f"{REGNSKAP}/{orgnr}", accept="application/json")
    return (r.json() if r.status == 200 and r.body.strip() else []), r


def _num(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d if isinstance(d, (int, float)) else None


FIN_FIELDS = {
    "revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
    "operating_result": ("resultatregnskapResultat", "driftsresultat", "driftsresultat"),
    "result_before_tax": ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
    "net_result": ("resultatregnskapResultat", "aarsresultat"),
    "total_assets": ("eiendeler", "sumEiendeler"),
    "equity": ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
    "total_debt": ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
    "cash": ("eiendeler", "sumBankinnskuddOgKontanter"),
}


def accounts_claims(cs: ClaimSet, orgnr: str, rows: list, r: Response) -> list[dict]:
    """Publish filed figures exactly as reported (currency as filed). A figure absent from the filing is
    recorded as not_available, never as zero. Returns the per-year series used for derived signals."""
    S = "annual_accounts"
    url = f"{REGNSKAP}/{orgnr}"
    series = []
    for row in sorted(rows, key=lambda x: (x.get("regnskapsperiode") or {}).get("tilDato", "")):
        per = row.get("regnskapsperiode") or {}
        period = f"{per.get('fraDato')}/{per.get('tilDato')}"
        kind = (row.get("regnskapstype") or "").lower()  # selskap (company) or konsern (group)
        y = {"period": period, "year": (per.get("tilDato") or "")[:4], "type": kind, "currency": row.get("valuta")}
        for name, path in FIN_FIELDS.items():
            v = _num(row, *path)
            y[name] = v
            fieldname = f"{name}" if kind == "selskap" else f"group_{name}"
            if v is None:
                cs.missing(S, fieldname, "not_available", f"Not reported in the filed {kind} accounts for {period}.")
                continue
            span = f"journalnr {row.get('journalnr')}, {'.'.join(path)}: {v:.0f} {row.get('valuta')}"
            cs.add(S, fieldname, {"amount": v, "currency": row.get("valuta")}, [_ev(cs, r, url, span, ACCOUNTS, period)],
                   reporting_period=period)
        audit = row.get("revisjon") or {}
        if audit:
            cs.add(S, "audit_opt_out" if kind == "selskap" else "group_audit_opt_out", bool(audit.get("fravalgRevisjon")),
                   [_ev(cs, r, url, f"journalnr {row.get('journalnr')}, revisjon.fravalgRevisjon: {audit.get('fravalgRevisjon')}", ACCOUNTS, period)],
                   reporting_period=period)
        series.append(y)
    if not rows:
        cs.missing(S, "revenue", "not_available", "Regnskapsregisteret returned no filed accounts for this entity.")
    return series


def fetch_roles(f: Fetcher, orgnr: str):
    r = f.get(f"{ENHET}/{orgnr}/roller", accept="application/json")
    return (r.json() if r.status == 200 else None), r


def _person(role: dict) -> str | None:
    p = role.get("person")
    if p:
        n = p.get("navn") or {}
        return " ".join(x for x in [n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")] if x) or None
    en = role.get("enhet")
    if en:
        return " ".join(en.get("navn") or []) + (f" (org. {en.get('organisasjonsnummer')})" if en.get("organisasjonsnummer") else "")
    return None


def roles_claims(cs: ClaimSet, orgnr: str, data: dict | None, r: Response):
    S = "leadership_workplaces"
    url = f"{ENHET}/{orgnr}/roller"
    found = set()
    for g in (data or {}).get("rollegrupper", []):
        for role in g.get("roller", []):
            code = (role.get("type") or {}).get("kode")
            if code not in ROLE_FIELDS or role.get("avregistrert") or (role.get("person") or {}).get("erDoed"):
                continue
            name = _person(role)
            if not name:
                continue
            field = ROLE_FIELDS[code]
            found.add(field)
            span = f"{(role.get('type') or {}).get('beskrivelse')}: {name}"
            cs.add(S, field, name, [_ev(cs, r, url, span)], effective_date=g.get("sistEndret"))
    if "ceo" not in found:
        cs.missing(S, "ceo", "not_available", "No registered general manager (daglig leder) in the roles register.")
    if "board_chair" not in found:
        cs.missing(S, "board_chair", "not_available", "No registered board chair in the roles register.")


def fetch_subunits(f: Fetcher, orgnr: str):
    r = f.get(f"{UNDERENHET}?overordnetEnhet={orgnr}&size=100", accept="application/json")
    if r.status != 200:
        return None, r
    d = r.json()
    return {"units": (d.get("_embedded") or {}).get("underenheter", []), "total": (d.get("page") or {}).get("totalElements", 0)}, r


def subunit_claims(cs: ClaimSet, orgnr: str, data, r: Response):
    S = "leadership_workplaces"
    url = f"{UNDERENHET}?overordnetEnhet={orgnr}"
    if data is None:
        cs.missing(S, "registered_workplaces", "failed", f"Subunit lookup returned HTTP {r.status}.")
        return
    cs.add(S, "registered_workplace_count", data["total"], [_ev(cs, r, url, f"page.totalElements: {data['total']}")])
    for u in data["units"][:25]:
        addr = _addr(u.get("beliggenhetsadresse")) or _addr(u.get("postadresse"))
        v = {"organisation_number": u.get("organisasjonsnummer"), "name": u.get("navn"), "address": addr,
             "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") else None}
        cs.add(S, "registered_workplace", v, [_ev(cs, r, url, f"underenhet {u.get('organisasjonsnummer')}: {u.get('navn')}, {addr}")],
               effective_date=u.get("oppstartsdato"))
