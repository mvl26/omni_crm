"""Dựng, đối chiếu và dọn dữ liệu TỔNG HỢP cho tải kênh quảng cáo (V2-08). Chạy trong bench, KHÔNG thuộc run-tests.

	bench --site omni.test execute omni_crm.tests.load.ads_setup.setup
	bench --site omni.test execute omni_crm.tests.load.ads_setup.verify --kwargs '{"expected": 200, "wait_minutes": 10}'
	bench --site omni.test execute omni_crm.tests.load.ads_setup.cleanup

`setup` tạo: 5 Sales User, 3 kênh quảng cáo + 1 kênh Zalo (sandbox bật, không token), 5 biểu mẫu (mỗi biểu mẫu một
`default_owner`), 40 Contact trùng người sẽ gửi form (20%), và NÂNG giới hạn tần suất form website (một IP duy nhất) — giá trị cũ
được ghi vào tệp để `cleanup` khôi phục đúng. Mọi tên có tiền tố LOADADS. Không gửi gì ra ngoài.
"""

from __future__ import annotations

import json
import math
import os
import time

import frappe

from omni_crm.tests.load import ads_load_common as C

STATE = "/tmp/claude-1000/v208/ads_setup_state.json"
RATE_FIELDS = ("webform_rate_limit", "webform_rate_window_minutes", "webform_global_rate_per_hour")
LEAD = "CRM Lead"


def _pct(vals, p):
	if not vals:
		return None
	vals = sorted(vals)
	return round(vals[max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))], 2)


def setup() -> None:
	"""Tạo dữ liệu tải. Chạy lại được (bỏ qua cái đã có)."""
	frappe.set_user("Administrator")
	saved = {}
	for f in RATE_FIELDS:
		r = frappe.db.sql("select value from `tabSingles` where doctype='Omni Settings' and field=%s", f)
		saved[f] = r[0][0] if r else None
	if not os.path.exists(STATE):
		json.dump(saved, open(STATE, "w"))
	for f, v in (("webform_rate_limit", 100000), ("webform_global_rate_per_hour", 100000)):
		frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field=%s", f)
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values ('Omni Settings', %s, %s)", (f, v)
		)
	for email in C.SALES:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": f"{C.PREFIX} Sale",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)
	chans = {
		C.CHANNELS["meta"]: (
			"Meta Lead Ads",
			{
				"meta_page_id": C.META_PAGE,
				"meta_app_secret": C.APP_SECRET,
				"meta_verify_token": C.VERIFY_TOKEN,
				"meta_page_access_token": C.PAGE_TOKEN,
			},
		),
		C.CHANNELS["google"]: ("Google Lead Form", {}),
		C.CHANNELS["web"]: ("Web Form", {}),
		C.CHANNELS["zalo"]: (
			"Zalo OA",
			{"zalo_app_id": C.ZALO_APP_ID, "zalo_oa_secret_key": C.ZALO_SECRET},
		),
	}
	for name, (ctype, extra) in chans.items():
		if not frappe.db.exists("Omni Channel", name):
			frappe.get_doc(
				{
					"doctype": "Omni Channel",
					"channel_name": name,
					"channel_type": ctype,
					"enabled": 1,
					**extra,
				}
			).insert()
	forms = (
		[("Meta", f) for f in C.META_FORMS]
		+ [("Google", f) for f in C.GOOGLE_FORMS]
		+ [("Website", f) for f in C.WEB_FORMS]
	)
	for n, (platform, ext) in enumerate(forms):
		if frappe.db.exists("Omni Lead Form", {"external_form_id": ext, "platform": platform}):
			continue
		doc = {
			"doctype": "Omni Lead Form",
			"platform": platform,
			"external_form_id": ext,
			"form_name": f"{C.PREFIX} {ext}",
			"enabled": 1,
			"default_owner": C.SALES[n % len(C.SALES)],
		}
		if platform == "Google":
			doc["secret"] = C.GOOGLE_KEY
		frappe.get_doc(doc).insert()
	for i in range(1, 201):
		if not C.has_contact(i):
			continue
		p = C.person(i)
		if frappe.db.exists("Contact", {"first_name": p["name"]}):
			continue
		frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": p["name"],
				"email_ids": [{"email_id": p["email"], "is_primary": 1}],
				"phone_nos": [{"phone": p["phone"], "is_primary_mobile_no": 1}],
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()
	print(json.dumps({"ok": True, "saved_rate_settings": saved}))


def _inbound_filters():
	return ["in", list(C.CHANNELS.values())]


def verify(expected: int = 200, wait_minutes: int = 10, channels: str = "meta,google,web") -> None:
	"""Chờ hàng đợi cạn rồi in số đo: mất, trùng, người phụ trách, thông báo. `expected` = số người duy nhất đã gửi."""
	chs = [C.CHANNELS[c] for c in channels.split(",")]
	t0 = time.time()

	def counts():
		return dict(
			frappe.db.sql(
				"select status, count(*) from `tabOmni Inbound Message` where channel in %s group by status",
				(chs,),
			)
		)

	c = counts()
	while sum(c.get(s, 0) for s in ("New", "Processing", "Retry")) and time.time() - t0 < wait_minutes * 60:
		time.sleep(5)
		frappe.db.rollback()
		c = counts()
	drain = round(time.time() - t0, 1)
	leads = frappe.db.sql_list(
		"select distinct lead from `tabOmni Inbound Message` where channel in %s and lead is not null", (chs,)
	)
	total = sum(c.values())
	dedupe_dups = frappe.db.sql(
		"select count(*) from (select dedupe_key from `tabOmni Inbound Message` where channel in %s "
		"group by dedupe_key having count(*)>1) t",
		(chs,),
	)[0][0]
	# Lead trùng: cùng SĐT chuẩn hóa hoặc cùng email -> nhiều hơn 1 Lead
	rows = frappe.db.sql(
		"select mobile_no, email, name from `tabCRM Lead` where name in %s", (leads or [""],), as_dict=True
	)
	by_phone: dict[str, int] = {}
	for r in rows:
		k = r.mobile_no or r.email or r.name
		by_phone[k] = by_phone.get(k, 0) + 1
	dup_leads = sum(1 for v in by_phone.values() if v > 1)
	multi_ident = frappe.db.sql(
		"select count(*) from (select identity from `tabOmni Inbound Message` where channel in %s and lead is not null "
		"group by identity having count(distinct lead)>1) t",
		(chs,),
	)[0][0]
	# người phụ trách: ToDo mở của Lead, thời gian từ tin vào -> ToDo
	todo = frappe.db.sql(
		"select l.name, min(m.creation) rec, min(t.creation) assigned, count(distinct t.allocated_to) owners "
		"from `tabCRM Lead` l join `tabOmni Inbound Message` m on m.lead = l.name and m.channel in %s "
		"left join `tabToDo` t on t.reference_type='CRM Lead' and t.reference_name = l.name and t.status='Open' "
		"where l.name in %s group by l.name",
		(chs, leads or [""]),
		as_dict=True,
	)
	no_owner = [r.name for r in todo if not r.assigned]
	secs = [(r.assigned - r.rec).total_seconds() for r in todo if r.assigned]
	notif = frappe.db.sql(
		"select count(*) from `tabNotification Log` where document_type='CRM Lead' and document_name in %s "
		"and subject like 'Lead mới từ quảng cáo%%'",
		(leads or [""],),
	)[0][0]
	crm_notif = frappe.db.sql(
		"select count(*) from `tabCRM Notification` where reference_doctype='CRM Lead' and reference_name in %s",
		(leads or [""],),
	)[0][0]
	done_secs = [
		float(r[0])
		for r in frappe.db.sql(
			"select timestampdiff(second, creation, modified) from `tabOmni Inbound Message` where channel in %s and status='Done'",
			(chs,),
		)
		if r[0] is not None
	]
	print(
		json.dumps(
			{
				"status_counts": c,
				"waited_for_drain_s": drain,
				"drained": not sum(c.get(s, 0) for s in ("New", "Processing", "Retry")),
				"expected_unique_people": expected,
				"inbound_total": total,
				"lost": max(0, expected - total),
				"extra_rows": max(0, total - expected),
				"dedupe_key_duplicates": dedupe_dups,
				"leads": len(leads),
				"duplicate_leads_same_phone_or_email": dup_leads,
				"identities_with_more_than_one_lead": multi_ident,
				"leads_without_owner": len(no_owner),
				"received_to_assigned_s": {
					"n": len(secs),
					"p50": _pct(secs, 0.5),
					"p95": _pct(secs, 0.95),
					"max": max(secs) if secs else None,
				},
				"notification_log_new_lead": notif,
				"crm_notification_rows": crm_notif,
				"receive_to_done_s": {"p50": _pct(done_secs, 0.5), "p95": _pct(done_secs, 0.95)},
			},
			ensure_ascii=False,
			indent=1,
			default=str,
		)
	)


def cleanup() -> None:
	"""Xóa MỌI dữ liệu LOADADS và khôi phục giới hạn tần suất form; commit. In số bản ghi đã xóa."""
	frappe.db.rollback()
	frappe.set_user("Administrator")
	P = C.PREFIX
	chs = frappe.get_all("Omni Channel", filters={"channel_name": ["like", f"{P}%"]}, pluck="name")
	idents = (
		frappe.get_all("Omni Identity", filters={"channel": ["in", chs or [""]]}, pluck="name")
		if frappe.get_meta("Omni Identity").has_field("channel")
		else []
	)
	idents += frappe.get_all("Omni Identity", filters={"identity_key": ["like", f"%{P}%"]}, pluck="name")
	leads = set(
		frappe.db.sql_list(
			"select distinct lead from `tabOmni Inbound Message` where channel in %s and lead is not null",
			(chs or [""],),
		)
	)
	leads.update(frappe.get_all("Omni Identity", filters={"name": ["in", idents or [""]]}, pluck="lead"))
	leads.update(frappe.get_all(LEAD, filters={"first_name": ["like", f"{P}%"]}, pluck="name"))
	leads.update(frappe.get_all(LEAD, filters={"facebook_lead_id": ["like", f"{P}%"]}, pluck="name"))
	leads.update(frappe.get_all(LEAD, filters={"organization": ["like", f"%{P}%"]}, pluck="name"))
	leads.discard(None)
	ll = sorted(leads) or [""]
	deals = frappe.get_all("CRM Deal", filters={"lead": ["in", ll]}, pluck="name")
	refs = ll + deals
	n = {"leads": len(leads)}
	for dt, f in (
		("Comment", "reference_name"),
		("Communication", "reference_name"),
		("ToDo", "reference_name"),
	):
		frappe.db.delete(dt, {f: ["in", refs]})
	frappe.db.delete("Notification Log", {"document_name": ["in", refs]})
	n["crm_notification"] = frappe.db.count("CRM Notification", {"reference_name": ["in", refs]})
	frappe.db.delete("CRM Notification", {"reference_name": ["in", refs]})
	for dt in (
		"Omni Care Log",
		"Omni Bot Draft",
		"Omni Bot Run",
	):  # tải Zalo có bot Draft/Handoff (FakeAIClient)
		frappe.db.delete(dt, {"lead": ["in", ll]})
	n["bot_runs_and_drafts_deleted_by_lead"] = True
	frappe.db.delete("Communication Link", {"link_doctype": LEAD, "link_name": ["in", ll]})
	frappe.db.delete("Omni Inbound Message", {"channel": ["in", chs or [""]]})
	frappe.db.delete("Omni Outbound Message", {"channel": ["in", chs or [""]]})
	for f in ("lead_a", "lead_b"):
		frappe.db.delete("Omni Duplicate Candidate", {f: ["in", ll]})
	frappe.db.delete("Omni Identity", {"name": ["in", idents or [""]]})
	frappe.db.delete("CRM Deal", {"name": ["in", deals or [""]]})
	for i, lead in enumerate(sorted(leads)):
		frappe.delete_doc(LEAD, lead, force=True, ignore_permissions=True, delete_permanently=True)
		if i % 50 == 49:
			frappe.db.commit()
	contacts = frappe.get_all("Contact", filters={"first_name": ["like", f"{P}%"]}, pluck="name")
	for c in contacts:
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True, delete_permanently=True)
	n["contacts"] = len(contacts)
	for org in frappe.get_all(
		"CRM Organization", filters={"organization_name": ["like", f"%{P}%"]}, pluck="name"
	):
		frappe.delete_doc(
			"CRM Organization", org, force=True, ignore_permissions=True, delete_permanently=True
		)
	forms = frappe.get_all("Omni Lead Form", filters={"form_name": ["like", f"{P}%"]}, pluck="name")
	if forms:
		frappe.db.delete("Omni Lead Form Field", {"parent": ["in", forms]})
		frappe.db.delete("__Auth", {"doctype": "Omni Lead Form", "name": ["in", forms]})
		frappe.db.delete("Omni Lead Form", {"name": ["in", forms]})
	frappe.db.delete("Omni Campaign", {"campaign_name": ["like", f"%{P}%"]})
	frappe.db.delete("Omni Campaign", {"external_campaign_id": ["like", f"{P}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["in", chs or [""]]})
	frappe.db.delete("Omni Channel", {"name": ["in", chs or [""]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", C.SALES]})
	for u in C.SALES:
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True, delete_permanently=True)
	for source in ("Quảng cáo Meta", "Quảng cáo Google", "Website"):
		if frappe.db.exists("CRM Lead Source", source) and not frappe.db.count(LEAD, {"source": source}):
			frappe.delete_doc("CRM Lead Source", source, force=True, ignore_permissions=True)
	if os.path.exists(STATE):
		saved = json.load(open(STATE))
		for f in RATE_FIELDS:
			frappe.db.sql("delete from `tabSingles` where doctype='Omni Settings' and field=%s", f)
			if saved.get(f) is not None:
				frappe.db.sql(
					"insert into `tabSingles` (doctype, field, value) values ('Omni Settings', %s, %s)",
					(f, saved[f]),
				)
		os.remove(STATE)
	frappe.db.commit()
	frappe.cache().delete_keys("omni:")
	print(json.dumps({"deleted": n}))


def reconcile_with_fake_graph(url: str = "http://127.0.0.1:8777") -> None:
	"""Chạy đối soát Meta của kênh LOADADS nhưng trỏ client vào máy chủ Graph GIẢ (không bao giờ gọi graph.facebook.com)."""
	from omni_crm.channels.meta_leads import client as meta_client
	from omni_crm.channels.meta_leads import reconcile

	meta_client.API_BASE = url.rstrip("/")
	print(json.dumps(reconcile.reconcile_channel(C.CHANNELS["meta"])))
