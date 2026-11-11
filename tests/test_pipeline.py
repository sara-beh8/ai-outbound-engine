import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline  # noqa: E402

GOOD_AI = {
    "subject": "Order status tickets at Nordlicht",
    "opener": "Nordlicht handled 4,200 tickets last month and 6% were automated.",
    "proof_point_id": "pp_order_status",
}

ACCOUNT = {
    "account_id": "ACC-1", "company": "Nordlicht", "motion": "self_serve",
    "monthly_tickets": "4200", "automation_rate": "0.06", "top_ticket_topic": "Order status",
    "contact_email": "ops@nordlicht.example", "consent_marketing": "true", "last_contacted_days": "60",
}


def test_clean_output_passes():
    assert pipeline.guardrail_failures(GOOD_AI) == []


def test_link_written_by_model_is_held():
    bad = {**GOOD_AI, "opener": "See www.acme-helpdesk.example for details."}
    assert "model wrote a link" in pipeline.guardrail_failures(bad)


def test_invented_product_name_is_held():
    bad = {**GOOD_AI, "opener": "Try the new Acme Q&A Hub today."}
    assert any("unknown product name" in f for f in pipeline.guardrail_failures(bad))


def test_unknown_proof_point_and_long_subject_are_held():
    bad = {**GOOD_AI, "subject": "x" * 80, "proof_point_id": "pp_made_up"}
    fails = pipeline.guardrail_failures(bad)
    assert "subject missing or too long" in fails
    assert "unknown proof point" in fails


def test_no_consent_is_excluded():
    assert pipeline.eligibility({**ACCOUNT, "consent_marketing": "false"}) == "no marketing consent"


def test_recent_contact_is_excluded():
    assert pipeline.eligibility({**ACCOUNT, "last_contacted_days": "5"}) == "contacted recently"


def test_cta_comes_from_config_with_utms():
    event = pipeline.to_customerio_event(ACCOUNT, GOOD_AI)
    url = event["data"]["cta_url"]
    assert url.startswith(pipeline.GUARDRAILS["cta_urls"]["self_serve"])
    assert "utm_campaign=grw-lifecycle_outbound-signal_automation-gap" in url


def test_run_splits_ready_and_held():
    ready, held, excluded = pipeline.run([ACCOUNT], lambda acc: {**GOOD_AI, "proof_point_id": "nope"})
    assert not ready and len(held) == 1 and not excluded
