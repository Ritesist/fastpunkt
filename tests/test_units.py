import json

import pytest

from fastpunkt import orgnr as on
from fastpunkt import website as W
from fastpunkt.claims import ClaimSet
from fastpunkt.fetch import Fetcher, Response, UnsafeURL, check_url
from fastpunkt.summary import derive_signals, summarise


def test_mod11_validation():
    assert on.is_valid("923609016") and on.is_valid("914778271")
    assert not on.is_valid("923609017") and not on.is_valid("12345678") and not on.is_valid("abcdefghi")


@pytest.mark.parametrize("text", ["Org.nr: 923 609 016 MVA", "Organisasjonsnummer 923609016", "NO 923.609.016 MVA"])
def test_orgnr_mentions_common_forms(text):
    assert on.mentions(text, "923609016")


def test_orgnr_mention_requires_number_boundaries():
    assert on.mentions("Tel 1923609016 x", "923609016") is None
    assert on.mentions("Konto 9236090165", "923609016") is None


def test_unsafe_urls_are_refused():
    public = lambda h: True
    for u in ["file:///etc/passwd", "ftp://x.no/", "http://user:pw@x.no/", "http://x.no:8080/"]:
        with pytest.raises(UnsafeURL):
            check_url(u, public)
    with pytest.raises(UnsafeURL):
        check_url("http://intranet.local/", lambda h: False)  # resolves only to private addresses
    assert check_url("https://www.brreg.no/", public)


def test_budget_is_enforced():
    f = Fetcher(max_requests=0)
    from fastpunkt.fetch import BudgetExhausted
    with pytest.raises(BudgetExhausted):
        f.get("https://data.brreg.no/enhetsregisteret/api/enheter/923609016")


def test_candidates_skip_free_mail_and_order_registry_first():
    c = W.candidates({"navn": "Sandnes Elektriske AS", "hjemmeside": "www.sandnes-el.no", "epostadresse": "post@gmail.com"})
    assert c[0] == {"url": "https://www.sandnes-el.no/", "host": "www.sandnes-el.no", "method": "registry_listed"}
    assert all(x["method"] != "registry_email_domain" for x in c)
    c2 = W.candidates({"navn": "Bølgen Bad AS", "epostadresse": "post@bolgenbad.no"})
    assert c2[0]["host"] == "bolgenbad.no" and c2[0]["method"] == "registry_email_domain"


def page(html, url="https://firma.no/"):
    return W.parse_page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-10-08T05:00:00Z", "abc", 0))


def test_page_extraction_social_contacts_jobs_dates():
    pg = page("""<html><head><title>Firma AS</title><meta name="description" content="Firma AS leverer elektrotjenester til bedrifter i Rogaland.">
      <script type="application/ld+json">{"@type":"JobPosting","title":"Elektriker","datePosted":"2026-09-30"}</script></head>
      <body><a href="https://www.linkedin.com/company/firma-as/">LinkedIn</a><a href="https://facebook.com/sharer/sharer.php?u=x">Del</a>
      <a href="mailto:post@firma.no">E-post</a><a href="tel:+47 51 23 45 67">Ring</a>
      <article><h3><a href="/nyheter/ny-avtale">Firma signerer ny rammeavtale med kommunen</a></h3><time datetime="2026-09-14">14.09.2026</time></article>
      <footer>Firma AS · Org.nr 810 034 882</footer></body></html>""")
    assert W.orgnr_on_page(pg, "810034882")
    assert W.social_profiles(pg) == [("linkedin", "https://www.linkedin.com/company/firma-as", "LinkedIn")]
    assert W.contacts(pg) == {"emails": ["post@firma.no"], "phones": ["+4751234567"]}
    assert W.job_postings_jsonld(pg)[0]["title"] == "Elektriker"
    d = W.dated_items(pg)[0]
    assert d["date"] == "2026-09-14" and d["title"].startswith("Firma signerer") and d["url"].endswith("/nyheter/ny-avtale")


def test_other_orgnr_on_page_is_detected():
    pg = page("<body>Eies av Konsern ASA, org.nr. 923 609 016</body>")
    assert W.other_orgnrs(pg, "810034882") == ["923609016"]


def test_name_match_uses_distinctive_tokens():
    pg = page("<title>Fjellglød Hudpleie</title><body>Velkommen</body>")
    assert W.names_company(pg, "FJELLGLØD HOLDING AS")
    assert not W.names_company(pg, "FJELLTOPP HOLDING AS")


def test_missing_values_are_states_not_zero():
    cs = ClaimSet()
    c = cs.missing("annual_accounts", "revenue", "not_available", "not reported")
    assert c["value"] is None and c["availability"] == "not_available" and c["evidence_ids"] == []
    with pytest.raises(AssertionError):
        cs.add("annual_accounts", "revenue", None, [])


def test_signals_and_summary_are_computed_from_claims():
    cs = ClaimSet()
    e1 = cs.add_evidence(source_url="u", source_class="official_registry", source_name="r", retrieved_at="t", content_sha256="h",
                         claim_span="s", extraction_method="json_field")
    cs.add("legal_identity", "legal_name", "TEST AS", [e1])
    cs.add("legal_identity", "legal_form", "AS (Aksjeselskap)", [e1])
    for per, v in (("2024-01-01/2024-12-31", 100.0), ("2025-01-01/2025-12-31", 125.0)):
        cs.add("annual_accounts", "revenue", {"amount": v, "currency": "NOK"}, [e1], reporting_period=per)
    series = [{"period": "2024-01-01/2024-12-31", "type": "selskap", "currency": "NOK", "revenue": 100.0},
              {"period": "2025-01-01/2025-12-31", "type": "selskap", "currency": "NOK", "revenue": 125.0}]
    sig = derive_signals(cs, series)
    assert sig[0]["signal"] == "revenue_change" and sig[0]["change_pct"] == 25.0
    s = summarise(cs, {"legal_identity": {"state": "available"}, "website_profiles": {"state": "not_available", "reason": "none"}}, sig)
    assert "TEST AS is an aksjeselskap (AS)" in s["text"] and "+25.0%" in s["text"]
    assert all(x["claim_ids"] for x in s["sentences"])
    assert any(u.get("section") == "website_profiles" for u in s["unknowns"])
