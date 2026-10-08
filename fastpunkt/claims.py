"""Claim and evidence bookkeeping. Every published value carries evidence; absence is an explicit state."""
from __future__ import annotations

STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")


class ClaimSet:
    def __init__(self):
        self.claims: list[dict] = []
        self.evidence: list[dict] = []
        self._ev_by_key: dict[tuple, str] = {}

    def add_evidence(self, *, source_url, source_class, source_name, retrieved_at, content_sha256,
                     claim_span, extraction_method, licence=None, reporting_period=None) -> str:
        key = (source_url, claim_span, extraction_method)
        if key in self._ev_by_key:
            return self._ev_by_key[key]
        eid = f"ev-{len(self.evidence) + 1}"
        e = {"id": eid, "source_url": source_url, "source_class": source_class, "source_name": source_name,
             "retrieved_at": retrieved_at, "content_sha256": content_sha256, "claim_span": claim_span,
             "extraction_method": extraction_method}
        if licence:
            e["licence"] = licence
        if reporting_period:
            e["reporting_period"] = reporting_period
        self.evidence.append(e)
        self._ev_by_key[key] = eid
        return eid

    def add(self, section: str, field: str, value, evidence_ids, *, confidence=1.0, availability="available", **extra) -> dict:
        assert availability in STATES
        if availability == "available":
            assert value is not None and evidence_ids, f"{field}: an available claim needs a value and evidence"
        c = {"id": f"c-{len(self.claims) + 1}", "section": section, "field": field, "value": value,
             "availability": availability, "confidence": confidence, "evidence_ids": list(evidence_ids)}
        c.update({k: v for k, v in extra.items() if v is not None})
        self.claims.append(c)
        return c

    def missing(self, section: str, field: str, availability: str, reason: str) -> dict:
        """Record that a field was checked (or could not be checked) without a value. Never a zero."""
        assert availability != "available"
        return self.add(section, field, None, [], confidence=None, availability=availability, reason=reason)

    def get(self, field: str):
        return [c for c in self.claims if c["field"] == field and c["availability"] == "available"]

    def first(self, field: str):
        g = self.get(field)
        return g[0]["value"] if g else None
