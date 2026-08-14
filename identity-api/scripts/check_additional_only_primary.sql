-- Read-only audit: is anyone holding an ADDITIONAL-ONLY role as their PRIMARY role?
--
-- Migration 0009 stops NEW assignments (put_user_roles now rejects them), but it
-- deliberately does not rewrite existing users — for payment_officer that would
-- silently strip someone's payment authority mid-flight. Run this before/after
-- deploying and fix any rows by hand in Portal → Access Control: set a real
-- primary role, then tick the additional-only role under Additional Roles.
--
--   psql "$DSN" -f scripts/check_additional_only_primary.sql

\echo '== Users whose PRIMARY role is additional-only (expected: 0 rows) =='
SELECT u.id, u.email, u.full_name, u.role AS primary_role, u.is_active
FROM users u
JOIN role_defs rd ON rd.code = u.role
WHERE rd.assignable_as_primary = false
ORDER BY u.is_active DESC, u.email;

\echo '== Roles flagged additional-only (expected: erp_pa_officer, payment_officer) =='
SELECT code, label, is_active FROM role_defs
WHERE assignable_as_primary = false ORDER BY code;

\echo '== Who holds them correctly, as ADDITIONAL roles (informational) =='
SELECT ur.role_code, u.email, u.full_name, u.role AS primary_role
FROM user_roles ur
JOIN role_defs rd ON rd.code = ur.role_code AND rd.assignable_as_primary = false
JOIN users u ON u.id = ur.user_id
ORDER BY ur.role_code, u.email;
