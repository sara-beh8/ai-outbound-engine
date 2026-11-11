-- account_signals.sql  (BigQuery Standard SQL)
-- Finds accounts with an "automation gap": high ticket volume, low automation,
-- marketing consent, and no recent outreach. Output feeds the Cargo play.
-- All tables and columns are fictional (acme_*); the shape is what matters.

DECLARE min_monthly_tickets INT64 DEFAULT 500;
DECLARE max_automation_rate FLOAT64 DEFAULT 0.15;
DECLARE cooldown_days INT64 DEFAULT 30;

WITH ticket_stats AS (
  SELECT
    account_id,
    COUNT(*) AS monthly_tickets,
    SAFE_DIVIDE(COUNTIF(resolved_by = 'automation'), COUNT(*)) AS automation_rate
  FROM `acme.support.tickets`
  WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY account_id
),

top_topic AS (
  SELECT account_id, topic AS top_ticket_topic
  FROM (
    SELECT account_id, topic,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY COUNT(*) DESC) AS rn
    FROM `acme.support.tickets`
    WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    GROUP BY account_id, topic
  )
  WHERE rn = 1
),

last_touch AS (
  SELECT account_id, DATE_DIFF(CURRENT_DATE(), MAX(DATE(sent_at)), DAY) AS last_contacted_days
  FROM `acme.marketing.email_deliveries`
  GROUP BY account_id
)

SELECT
  a.account_id,
  a.company_name AS company,
  a.domain,
  a.plan,
  IF(a.arr_usd >= 20000, 'sales_led', 'self_serve') AS motion,
  t.monthly_tickets,
  ROUND(t.automation_rate, 3) AS automation_rate,
  tt.top_ticket_topic,
  c.email AS contact_email,
  c.first_name AS contact_first_name,
  c.marketing_consent AS consent_marketing,
  COALESCE(lt.last_contacted_days, 999) AS last_contacted_days
FROM `acme.crm.accounts` a
JOIN ticket_stats t USING (account_id)
JOIN top_topic tt USING (account_id)
JOIN `acme.crm.primary_contacts` c USING (account_id)
LEFT JOIN last_touch lt USING (account_id)
WHERE a.status = 'active'
  AND c.marketing_consent                           -- legitimate data only
  AND c.email IS NOT NULL
  AND t.monthly_tickets >= min_monthly_tickets
  AND t.automation_rate < max_automation_rate
  AND COALESCE(lt.last_contacted_days, 999) >= cooldown_days
ORDER BY t.monthly_tickets DESC;
