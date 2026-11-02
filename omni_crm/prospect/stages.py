"""Giai đoạn bán hàng B2B (docs/14 mục 4) và lý do "Không phù hợp" — chỉ THÊM dữ liệu CRM, không đổi/xóa gì có sẵn.

CRM Lead Status / CRM Deal Status / CRM Lost Reason là dữ liệu (không sửa code `crm`). Chạy lại nhiều lần không lỗi.
Bảng ánh xạ trạng thái cũ -> mới ở đây chỉ để IN RA cho người duyệt; KHÔNG có hàm nào đổi trạng thái của Lead/Deal.
"""

from __future__ import annotations

import frappe

LEAD_STATUS = "CRM Lead Status"
DEAL_STATUS = "CRM Deal Status"
LOST_REASON = "CRM Lost Reason"
NOT_FIT = "Không phù hợp"

# (tên, type của CRM, màu)
B2B_LEAD_STATUSES: list[tuple[str, str, str]] = [
	("Tiềm năng", "Open", "gray"),
	("Đã liên hệ", "Ongoing", "blue"),
	("Đã trao đổi nhu cầu", "Ongoing", "cyan"),
	(NOT_FIT, "Lost", "red"),
]
# (tên, type, màu, xác suất %)
B2B_DEAL_STATUSES: list[tuple[str, str, str, float]] = [
	("Đề xuất gói", "Ongoing", "violet", 40),
	("Đàm phán", "Ongoing", "orange", 70),
	("Ký hợp đồng", "Won", "green", 100),
	(NOT_FIT, "Lost", "red", 0),
]
# Lý do "Không phù hợp" (docs/14 mục 4); "Khác" dùng `Other` có sẵn của CRM (ĐQ-8: không dùng omni_lost_reason).
B2B_LOST_REASONS = ["Không có nhu cầu", "Đã tự vận hành", "Quy mô nhỏ", "Không liên lạc được"]

# ĐỀ XUẤT ánh xạ trạng thái hiện có -> giai đoạn B2B. Chờ anh duyệt; không hàm nào áp dụng nó.
PROPOSED_LEAD_MAPPING: dict[str, tuple[str, str]] = {
	"New": ("Tiềm năng", "Chưa liên hệ"),
	"Contacted": ("Đã liên hệ", "Đã có liên hệ"),
	"Nurture": ("Đã liên hệ", "Nuôi dưỡng = đã liên hệ, chưa sẵn sàng"),
	"Qualified": (
		"Đã trao đổi nhu cầu",
		"CRM đặt Qualified khi chuyển Lead thành Deal; giai đoạn thật nằm ở Deal",
	),
	"Converted": ("Đã trao đổi nhu cầu", "Đã chuyển Deal; giai đoạn thật nằm ở Deal"),
	"Unqualified": (NOT_FIT, "Giữ nguyên lý do mất hiện có"),
	"Junk": ("(giữ nguyên Junk)", "Rác/thử/Lead đã gộp, không phải nhà thầu 'không phù hợp'"),
}
PROPOSED_DEAL_MAPPING: dict[str, tuple[str, str]] = {
	"Qualification": (
		"Đã trao đổi nhu cầu",
		"Chưa gửi đề xuất (docs/14 mục 4: Đề xuất gói chỉ khi đã gửi đề xuất)",
	),
	"Demo/Making": ("Đã trao đổi nhu cầu", "Chưa gửi đề xuất"),
	"Proposal/Quotation": ("Đề xuất gói", "Đã báo giá/đề xuất"),
	"Negotiation": ("Đàm phán", ""),
	"Ready to Close": ("Đàm phán", ""),
	"Won": ("Ký hợp đồng", ""),
	"Lost": (NOT_FIT, "Gộp cả thua đối thủ lẫn không có nhu cầu; giữ nguyên lý do mất hiện có để phân biệt"),
}


def _max_position(doctype: str) -> int:
	return int(frappe.db.sql(f"select ifnull(max(position), 0) from `tab{doctype}`")[0][0] or 0)


def ensure_stages() -> dict[str, list[str]]:
	"""Thêm giai đoạn B2B và lý do mất còn thiếu (xếp sau các trạng thái sẵn có, đúng thứ tự docs/14); trả bản ghi mới tạo."""
	created: dict[str, list[str]] = {"lead_status": [], "deal_status": [], "lost_reason": []}
	pos = _max_position(LEAD_STATUS)
	for name, kind, color in B2B_LEAD_STATUSES:
		pos += 1
		if frappe.db.exists(LEAD_STATUS, name):
			continue
		frappe.get_doc(
			{"doctype": LEAD_STATUS, "lead_status": name, "type": kind, "color": color, "position": pos}
		).insert(ignore_permissions=True)
		created["lead_status"].append(name)
	pos = _max_position(DEAL_STATUS)
	for name, kind, color, prob in B2B_DEAL_STATUSES:
		pos += 1
		if frappe.db.exists(DEAL_STATUS, name):
			continue
		frappe.get_doc(
			{
				"doctype": DEAL_STATUS,
				"deal_status": name,
				"type": kind,
				"color": color,
				"position": pos,
				"probability": prob,
			}
		).insert(ignore_permissions=True)
		created["deal_status"].append(name)
	for reason in B2B_LOST_REASONS:
		if not frappe.db.exists(LOST_REASON, reason):
			frappe.get_doc({"doctype": LOST_REASON, "lost_reason": reason}).insert(ignore_permissions=True)
			created["lost_reason"].append(reason)
	return created


def status_counts() -> dict[str, dict[str, int]]:
	"""Số Lead (chưa bị gộp) theo trạng thái hiện tại, số Lead đã gộp (Lead phụ), và số Deal theo trạng thái."""
	leads = dict(
		frappe.db.sql(
			"""select status, count(*) from `tabCRM Lead`
			where ifnull(omni_merged_into, '') = '' group by status"""
		)
	)
	merged = frappe.db.count("CRM Lead", {"omni_merged_into": ["is", "set"]})
	deals = dict(frappe.db.sql("select status, count(*) from `tabCRM Deal` group by status"))
	return {"lead": leads, "deal": deals, "merged_secondary_leads": {"Junk (đã gộp)": merged}}


def mapping_table() -> list[dict]:
	"""Bảng ánh xạ ĐỀ XUẤT kèm số bản ghi hiện có ở từng trạng thái cũ (để anh duyệt trước khi áp dụng)."""
	counts = status_counts()
	rows: list[dict] = []
	for kind, mapping, have in (
		("Lead", PROPOSED_LEAD_MAPPING, counts["lead"]),
		("Deal", PROPOSED_DEAL_MAPPING, counts["deal"]),
	):
		for old, (new, note) in mapping.items():
			rows.append(
				{
					"loai": kind,
					"hien_tai": old,
					"de_xuat": new,
					"so_luong": int(have.get(old, 0)),
					"ghi_chu": note,
				}
			)
		for old, n in have.items():
			if old not in mapping:
				rows.append(
					{
						"loai": kind,
						"hien_tai": old,
						"de_xuat": "(giữ nguyên — không có trong bảng)",
						"so_luong": int(n),
						"ghi_chu": "Trạng thái tùy biến",
					}
				)
	rows.append(
		{
			"loai": "Lead",
			"hien_tai": "Junk (Lead phụ đã gộp)",
			"de_xuat": "(giữ nguyên Junk)",
			"so_luong": counts["merged_secondary_leads"]["Junk (đã gộp)"],
			"ghi_chu": "Loại khỏi bảng trên",
		}
	)
	return rows
