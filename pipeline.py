"""
ai-outbound-engine: BigQuery signals -> AI personalization -> guardrails -> Customer.io.

Runs the Cargo play from config/cargo_play.yaml locally, on a CSV export of
sql/account_signals.sql. Without an API key it uses a deterministic offline
writer, so the whole pipeline can be run and tested with no credentials.

Usage:
    python pipeline.py data/accounts_sample.csv --out data/example_payloads.jsonl
    # live AI: set ANTHROPIC_API_KEY and CLAUDE_MODEL, then add --live
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).parent


def _load(full: Path, example: Path) -> str:
    """Use the full file if present (available on request), else the public example."""
    return (full if full.exists() else example).read_text(encoding="utf-8")


GUARDRAILS = json.loads(_load(ROOT / "config" / "guardrails.json", ROOT / "config" / "guardrails.example.json"))
PROMPT = _load(ROOT / "templates" / "personalize_prompt.md", ROOT / "templates" / "personalize_prompt.example.md")

MIN_MONTHLY_TICKETS = 500
MAX_AUTOMATION_RATE = 0.15
COOLDOWN_DAYS = 30


# 1. Eligibility (re-checked here even though the SQL already filters: defense in depth)
def eligibility(acc: dict) -> str | None:
    """Return a reason the account is excluded, or None if it is eligible."""
    if acc["consent_marketing"].strip().lower() != "true":
        return "no marketing consent"
    if not acc["contact_email"].strip():
        return "no contact email"
    if int(acc["last_contacted_days"]) < COOLDOWN_DAYS:
        return "contacted recently"
    if int(acc["monthly_tickets"]) < MIN_MONTHLY_TICKETS:
        return "ticket volume below threshold"
    if float(acc["automation_rate"]) >= MAX_AUTOMATION_RATE:
        return "already automating"
    return None


# 2. Personalization
def build_prompt(acc: dict) -> str:
    g = GUARDRAILS
    return PROMPT.format(
        company=acc["company"],
        monthly_tickets=acc["monthly_tickets"],
        automation_rate_pct=f"{float(acc['automation_rate']):.0%}",
        top_ticket_topic=acc["top_ticket_topic"],
        motion=acc["motion"],
        proof_points="\n".join(f"- {k}: {v}" for k, v in g["proof_points"].items()),
        product_names=", ".join(g["product_names"]),
        banned_phrases=", ".join(g["banned_phrases"]),
        subject_max_chars=g["limits"]["subject_max_chars"],
        opener_max_chars=g["limits"]["opener_max_chars"],
    )


def offline_writer(acc: dict) -> dict:
    """Deterministic stand-in for the model, so the pipeline runs without credentials."""
    topic = acc["top_ticket_topic"]
    pp = "pp_order_status" if "order" in topic.lower() else "pp_auto_resolve"
    return {
        "subject": f"{topic} tickets at {acc['company']}"[:50],
        "opener": (
            f"{acc['company']} handled {int(acc['monthly_tickets']):,} tickets last month, "
            f"and only {float(acc['automation_rate']):.0%} were resolved automatically. "
            f"{topic} was the biggest category."
        ),
        "proof_point_id": pp,
    }


def live_writer(acc: dict) -> dict:
    import anthropic  # optional dependency, only needed with --live

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ["CLAUDE_MODEL"],
        max_tokens=400,
        messages=[{"role": "user", "content": build_prompt(acc)}],
    )
    text = msg.content[0].text
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


# 3. Guardrails: anything that fails is held for human review, never sent or auto-fixed
URL_RE = re.compile(r"https?://|www\.", re.I)
PRODUCT_RE = re.compile(r"\bAcme(?: [A-Z][A-Za-z]+)*")


def guardrail_failures(ai: dict) -> list[str]:
    g, fails = GUARDRAILS, []
    subject, opener = ai.get("subject", ""), ai.get("opener", "")
    if not subject or len(subject) > g["limits"]["subject_max_chars"]:
        fails.append("subject missing or too long")
    if not opener or len(opener) > g["limits"]["opener_max_chars"]:
        fails.append("opener missing or too long")
    if URL_RE.search(subject + " " + opener):
        fails.append("model wrote a link")
    for name in PRODUCT_RE.findall(subject + " " + opener):
        if name not in g["product_names"]:
            fails.append(f"unknown product name: {name}")
    if ai.get("proof_point_id") not in g["proof_points"]:
        fails.append("unknown proof point")
    text = (subject + " " + opener).lower()
    for phrase in g["banned_phrases"]:
        if phrase in text:
            fails.append(f"banned phrase: {phrase}")
    return fails


# 4. Customer.io payload (Track API event that triggers the campaign)
def cta_url(motion: str) -> str:
    return f"{GUARDRAILS['cta_urls'][motion]}?{urlencode(GUARDRAILS['utm'])}"


def to_customerio_event(acc: dict, ai: dict) -> dict:
    return {
        "customer_id": acc["account_id"],
        "name": "outbound_signal_ready",
        "data": {
            "subject": ai["subject"],
            "opener": ai["opener"],
            "proof_point": GUARDRAILS["proof_points"][ai["proof_point_id"]],
            "motion": acc["motion"],
            "cta_url": cta_url(acc["motion"]),
        },
    }


def run(accounts: list[dict], writer) -> tuple[list[dict], list[dict], list[dict]]:
    ready, held, excluded = [], [], []
    for acc in accounts:
        reason = eligibility(acc)
        if reason:
            excluded.append({"account_id": acc["account_id"], "reason": reason})
            continue
        ai = writer(acc)
        fails = guardrail_failures(ai)
        if fails:
            held.append({"account_id": acc["account_id"], "failures": fails, "ai": ai})
        else:
            ready.append(to_customerio_event(acc, ai))
    return ready, held, excluded


def main() -> None:
    p = argparse.ArgumentParser(description="Run the automation-gap outbound play on a CSV of accounts.")
    p.add_argument("accounts_csv")
    p.add_argument("--out", default="payloads.jsonl")
    p.add_argument("--live", action="store_true", help="use Claude instead of the offline writer")
    args = p.parse_args()

    with open(args.accounts_csv, newline="", encoding="utf-8") as f:
        accounts = list(csv.DictReader(f))

    ready, held, excluded = run(accounts, live_writer if args.live else offline_writer)
    with open(args.out, "w", encoding="utf-8") as f:
        for event in ready:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(f"{len(accounts)} accounts in  ->  {len(ready)} ready for Customer.io, "
          f"{len(held)} held for review, {len(excluded)} excluded")
    for e in excluded:
        print(f"  excluded {e['account_id']}: {e['reason']}")
    for h in held:
        print(f"  held {h['account_id']}: {'; '.join(h['failures'])}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
