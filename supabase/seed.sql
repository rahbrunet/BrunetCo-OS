-- Dev seeds (local + CI only — never production).
-- The SPA's dev-mode identity (apps/web/src/auth/msal.ts) is 11111111-...; seed it as a
-- Principal so the local stack is fully operable out of the box.

insert into app.os_users (id, email, display_name, role_template) values
  ('11111111-1111-1111-1111-111111111111', 'dev.user@brunetco.com',  'Dev Principal', 'Principal'),
  ('22222222-2222-2222-2222-222222222222', 'dev.agent@brunetco.com', 'Dev Agent',     'Agent')
on conflict (id) do nothing;

insert into app.permission_grants (user_id, domain, granted_by)
select u.id, d, '11111111-1111-1111-1111-111111111111'
  from app.os_users u
  join app.role_templates t on t.name = u.role_template
  cross join lateral unnest(t.domains) as d
on conflict do nothing;
