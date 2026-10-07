-- Run ONCE in each CRM's Supabase SQL editor (FMC and Admitverse separately).
-- Creates a strictly read-only role for the MIS sync worker. The MIS can never
-- mutate CRM data because this role has SELECT only.

CREATE ROLE mis_readonly LOGIN PASSWORD '<strong-password>';

GRANT CONNECT ON DATABASE postgres TO mis_readonly;
GRANT USAGE ON SCHEMA public TO mis_readonly;

-- Only the tables the sync actually reads.
GRANT SELECT ON public.leads, public.lead_sources, public.lead_stage_logs TO mis_readonly;

-- FundMyCampus ONLY: the partner-payout view (Payout page). Do not run on Admitverse.
-- GRANT SELECT ON public.mis_partner_payouts TO mis_readonly;

-- Optional: ensure future re-created tables of the same name stay readable.
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mis_readonly;

-- Verify (should show only SELECT):
-- SELECT grantee, privilege_type, table_name
-- FROM information_schema.role_table_grants
-- WHERE grantee = 'mis_readonly';
