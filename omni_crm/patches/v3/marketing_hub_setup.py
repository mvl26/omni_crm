"""V3-10: tạo vai trò Marketing Manager / Marketing User và ghi mặc định cho Omni Marketing Settings (dry_run BẬT)."""

from omni_crm.marketing import roles, settings


def execute() -> None:
	roles.ensure_roles()
	settings.ensure_initialized()
