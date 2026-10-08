# 100-company smoke test

Agent Fastpunkt 0.1.0, commit `ddb09122ca582ef7cf81e96b49c47e5711a2f3f0`. Input: `tests/fixtures/smoke-companies-100.jsonl`, produced by the starter kit's `select_entry_batch.py` (seed 20260823) from the official 411,160-company universe.

## First run

| Metric | Value |
|---|---|
| Run id | smoke-100 |
| Started / completed (UTC) | 2026-10-08T05:37:28Z / 2026-10-08T05:40:30Z |
| Inputs / terminal envelopes | 100 / 100 |
| Outbound HTTP requests | 548 of 2000 |
| Wall time | 181.6 s of 45 min |
| Per-company time p50 / p95 | 12.6 s / 23.4 s |
| TypeSafe calls / input tokens | 28 / 210,181 |
| Declared third-party cost | USD 0.0088 |
| Claims (all states) | 5,104 |
| Available claims without evidence | 0 |

## Section states

| Section | available | not_available | ambiguous | blocked | failed | not_applicable |
|---|---|---|---|---|---|---|
| legal_identity | 100 | 0 | 0 | 0 | 0 | 0 |
| annual_accounts | 100 | 0 | 0 | 0 | 0 | 0 |
| leadership_workplaces | 100 | 0 | 0 | 0 | 0 | 0 |
| website_profiles | 8 | 64 | 21 | 0 | 7 | 0 |
| hiring_activity | 0 | 97 | 3 | 0 | 0 | 0 |

## Companies with each field (available claims)

| Field | Companies |
|---|---|
| legal_name | 100 |
| industry | 100 |
| under_forced_dissolution | 100 |
| bankrupt | 100 |
| part_of_group | 100 |
| legal_form | 100 |
| result_before_tax | 100 |
| founded_date | 100 |
| municipality | 100 |
| registered_in_business_register | 100 |
| registration_date | 100 |
| net_result | 100 |
| under_liquidation | 100 |
| equity | 100 |
| registered_workplace_count | 100 |
| registered_activity | 100 |
| business_address | 100 |
| latest_submitted_accounts_year | 100 |
| total_assets | 100 |
| operating_result | 100 |
| vat_registered | 100 |
| organisation_number | 100 |
| audit_opt_out | 100 |
| board_chair | 99 |
| total_debt | 98 |
| cash | 97 |
| share_capital | 93 |
| revenue | 75 |
| registered_workplace | 73 |
| accountant | 67 |
| ceo | 65 |
| board_member | 49 |
| registry_phone | 29 |
| auditor | 26 |
| registry_email | 25 |
| postal_address | 25 |
| former_name | 18 |
| registered_employees | 10 |
| business_manager | 8 |
| official_website | 8 |
| website_title | 8 |
| registry_website | 7 |
| business_description | 4 |
| website_email | 2 |
| social_profile | 2 |
| group_revenue | 1 |
| group_audit_opt_out | 1 |
| group_result_before_tax | 1 |
| group_operating_result | 1 |
| group_net_result | 1 |
| group_total_debt | 1 |
| group_cash | 1 |
| group_equity | 1 |
| group_total_assets | 1 |
| news_page | 1 |
| general_partner | 1 |
| website_phone | 1 |

## Website identity

A website is published only with hard identity evidence. Verification methods used: orgnr_on_site 3, registry_listed 5. Sites that could not be tied to this exact legal entity are listed as candidates with state `ambiguous` and are not published.

## Refresh run (same inputs, same store)

| Metric | Value |
|---|---|
| Run id | smoke-100-refresh |
| Requests / wall time | 546 / 181.9 s |
| Declared third-party cost | USD 0.0085 |
| Refresh status | unchanged 100 |
| Material changes reported | 0 |

Snapshots are content-addressed and `history.jsonl` is append-only, so the first run's snapshots are preserved. A section that failed in one run is not treated as a removal.

