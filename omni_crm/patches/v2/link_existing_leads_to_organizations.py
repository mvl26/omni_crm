"""V2-02: liên kết Lead đang có với CRM Organization và Contact (xem prospect/linking.py).

KHÔNG đăng ký trong patches.txt: bench migrate sẽ chạy mọi patch trong đó thật ngay trên staging/production, bỏ qua cổng
duyệt của người phụ trách. Chạy bằng tay:

    # dry run (mặc định): chỉ in
    bench --site <site> execute omni_crm.patches.v2.link_existing_leads_to_organizations.execute
    # chạy thật, SAU KHI duyệt dry run và bảng ánh xạ trạng thái
    bench --site <site> execute omni_crm.patches.v2.link_existing_leads_to_organizations.execute --kwargs "{'dry_run': False}"
"""

from omni_crm.prospect import linking


def execute(dry_run: bool = True, create_from_domain: bool = True) -> dict:
	"""Chạy chuyển đổi; mặc định dry run. In báo cáo và trả dict."""
	if isinstance(dry_run, str):  # `--kwargs` của bench có thể đưa chuỗi
		dry_run = dry_run.strip().lower() not in ("0", "false", "no")
	report = linking.run(dry_run=bool(dry_run), create_from_domain=bool(create_from_domain))
	print(linking.format_report(report))
	return report
