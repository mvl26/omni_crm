"""Khóa bệnh viện (docs/14 mục 2.2): chuẩn hóa tên, áp bảng Omni Hospital Alias, liệt kê tên gần giống."""

from __future__ import annotations

from difflib import SequenceMatcher

import frappe

from omni_crm.prospect.normalize import hospital_key as normalize_hospital

AWARD = "Omni Bid Award"
ALIAS = "Omni Hospital Alias"
SIMILAR_FROM = 0.85  # docs/14 mục 2.2
MAX_SIMILAR_NAMES = 2000  # giới hạn số tên so cặp (O(n^2)) cho màn hình quản trị


def resolve_keys(hospital: str | None) -> tuple[str, str]:
	"""(hospital_raw_key, hospital_key): khóa trước và sau khi áp bảng alias (một bước, không đệ quy)."""
	raw = normalize_hospital(hospital)
	canonical = frappe.db.get_value(ALIAS, raw, "canonical_name") if raw else None
	return raw, (normalize_hospital(canonical) if canonical else raw)


def refresh_awards_for_alias(alias_key: str) -> list[str]:
	"""Đặt lại `hospital_key` của mọi gói có `hospital_raw_key` = alias_key; trả danh sách nhà thầu bị ảnh hưởng."""
	if not alias_key:
		return []
	canonical = frappe.db.get_value(ALIAS, alias_key, "canonical_name")
	new_key = normalize_hospital(canonical) if canonical else alias_key
	rows = frappe.get_all(AWARD, filters={"hospital_raw_key": alias_key}, fields=["name", "organization"])
	for r in rows:
		frappe.db.set_value(AWARD, r.name, "hospital_key", new_key, update_modified=False)
	return sorted({r.organization for r in rows})


def similar_hospital_names(threshold: float = SIMILAR_FROM) -> list[dict]:
	"""Cặp `hospital_key` khác nhau nhưng giống nhau >= threshold, để người duyệt gộp bằng Omni Hospital Alias."""
	rows = frappe.db.sql(
		f"""select hospital_key, min(hospital) as sample, count(*) as awards
		from `tab{AWARD}` where ifnull(hospital_key, '') != '' group by hospital_key limit {MAX_SIMILAR_NAMES}""",
		as_dict=True,
	)
	out: list[dict] = []
	for i, a in enumerate(rows):
		for b in rows[i + 1 :]:
			score = SequenceMatcher(None, a.hospital_key, b.hospital_key).ratio()
			if score >= threshold:
				out.append(
					{
						"a": a.hospital_key,
						"a_sample": a.sample,
						"a_awards": a.awards,
						"b": b.hospital_key,
						"b_sample": b.sample,
						"b_awards": b.awards,
						"similarity": round(score, 3),
					}
				)
	return sorted(out, key=lambda x: -x["similarity"])
