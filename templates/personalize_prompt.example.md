Simplified example prompt. The production prompt and its rules are available on request.

Write the first lines of a short B2B email for {company} ({motion}).

Facts you may use: {monthly_tickets} monthly tickets, {automation_rate_pct} resolved automatically, top topic {top_ticket_topic}.
Proof points (pick one id): {proof_points}
Allowed product names: {product_names}. Never use: {banned_phrases}.
Subject max {subject_max_chars} characters; opener max {opener_max_chars} characters. No links.

Return only JSON: {{"subject": "...", "opener": "...", "proof_point_id": "..."}}
