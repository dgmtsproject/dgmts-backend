BEGIN;
DELETE FROM tasks.notification_outbox WHERE recipient_email LIKE '%@tasktest.invalid';
DELETE FROM tasks.email_action_tokens WHERE task_id IN (SELECT id FROM tasks.tasks WHERE created_by LIKE 'tt-%' OR assigned_to LIKE 'tt-%' OR original_assignee LIKE 'tt-%');
DELETE FROM tasks.task_events WHERE task_id IN (SELECT id FROM tasks.tasks WHERE created_by LIKE 'tt-%' OR assigned_to LIKE 'tt-%' OR original_assignee LIKE 'tt-%');
DELETE FROM tasks.tasks WHERE created_by LIKE 'tt-%' OR assigned_to LIKE 'tt-%' OR original_assignee LIKE 'tt-%';
DELETE FROM tasks.app_user_roles WHERE employee_id LIKE 'tt-%';
DELETE FROM tasks.employee_emails WHERE employee_id LIKE 'tt-%';
DELETE FROM tasks.employees WHERE id LIKE 'tt-%';
COMMIT;
