"""Xử lý sự kiện `form_lead` trong worker (docs/15 mục 5-7): lấy dữ liệu Meta, ánh xạ, quy kết chiến dịch, giao sale.

Hai điểm nối với pipeline (qua adapter, pipeline không chứa logic riêng của kênh):
- `prepare_batch`: TRƯỚC resolver. Meta: gọi Graph API (worker đã commit trước nên không giữ khóa DB); tắt biểu mẫu -> bỏ khỏi
  lô (Done, không Lead); đặt `sender_profile` để resolver coi SĐT/email/MST là tín hiệu.
- `after_resolve`: SAU resolver. Ghi first/last touch, thông tin Lead, Organization chưa xác minh, đồng ý trên Contact,
  Lead thử nghiệm, giao sale và báo người phụ trách.

Không có đường gửi gì cho người điền biểu mẫu: chỉ ghi Lead và báo nội bộ.
Dữ liệu từ biểu mẫu là DỮ LIỆU: không nội dung nào được dùng làm lệnh/câu lệnh cho bot.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, escape_html, getdate, now_datetime

from omni_crm.channels import form_common
from omni_crm.channels.meta_leads.client import MetaClient
from omni_crm.channels.meta_leads.errors import TRANSIENT, MetaError
from omni_crm.crm_bridge import lead as lead_bridge
from omni_crm.identity.normalize import normalize_email, normalize_phone
from omni_crm.pipeline.retry import TransientError
from omni_crm.prospect import contacts as prospect_contacts
from omni_crm.prospect.normalize import InvalidTaxId, normalize_tax_id

LEAD = "CRM Lead"
INBOUND = "Omni Inbound Message"
FORM = "Omni Lead Form"
CAMPAIGN = "Omni Campaign"
ORG = "CRM Organization"
AD_PLATFORMS = ("Meta", "Google", "Website")
SOURCE_FIELDS = ("omni_ad_id", "omni_adset_id", "omni_form_id", "omni_click_id", "omni_landing_page")
DATA_SOURCE = "Biểu mẫu quảng cáo"
CONSENT_METHOD = "Biểu mẫu {0} «{1}»"
PROSPECT_STATUS = "Tiềm năng"
DATA_LEN = 140


# --- tra cứu ------------------------------------------------------------------------------------------------------


def get_form(platform: str, form_id: str | None):
	"""Omni Lead Form theo (nền tảng, mã biểu mẫu), None nếu chưa cấu hình."""
	from omni_crm.omni_crm.doctype.omni_lead_form.omni_lead_form import form_key

	key = form_key(platform, form_id)
	name = frappe.db.get_value(FORM, {"form_key": key}, "name") if key else None
	return frappe.get_doc(FORM, name) if name else None


def _raw(name: str) -> dict:
	value = frappe.db.get_value(INBOUND, name, "raw_payload")
	try:
		data = json.loads(value) if value else {}
	except ValueError:
		data = {}
	return data if isinstance(data, dict) else {}


def form_of(name: str) -> dict:
	"""Dict `form_lead` đã lưu của một tin vào (rỗng nếu không có)."""
	form = _raw(name).get("form_lead")
	return form if isinstance(form, dict) else {}


def _save_form(name: str, form: dict, text: str | None = None, profile: dict | None = None) -> None:
	values: dict[str, Any] = {"raw_payload": frappe.as_json({"form_lead": form})}
	if text is not None:
		values["text"] = text
	if profile is not None:
		values["sender_profile"] = frappe.as_json(profile)
	frappe.db.set_value(INBOUND, name, values, update_modified=False)


# --- Meta: dựng dữ liệu từ Graph API ------------------------------------------------------------------------------


def meta_form_from_graph(lead: dict, ad: dict, hook: dict, form_doc) -> dict:
	"""Dict `form_lead` từ phản hồi Graph (`lead`), thông tin quảng cáo (`ad`) và giá trị webhook (`hook`)."""
	answers = form_common.values(lead.get("field_data") or [])
	for item in lead.get("custom_disclaimer_responses") or []:
		if isinstance(item, dict) and item.get("checkbox_key"):
			answers[form_common.clean(item["checkbox_key"], 120)] = "1" if item.get("is_checked") else ""
	return form_common.build_form(
		"Meta",
		lead_id=str(lead.get("id") or hook.get("lead_id")),
		answers=answers,
		form_doc=form_doc,
		form_id=str(lead.get("form_id") or hook.get("form_id") or ""),
		extra={
			"fetched": True,
			"page_id": hook.get("page_id") or "",
			"ad_id": form_common.clean(lead.get("ad_id") or hook.get("ad_id"), 140),
			"adset_id": form_common.clean(ad.get("adset_id") or hook.get("adset_id"), 140),
			"adset_name": form_common.clean(ad.get("adset_name"), 140),
			"ad_name": form_common.clean(ad.get("ad_name"), 140),
			"campaign_ext_id": form_common.clean(ad.get("campaign_id"), 140),
			"campaign_name": form_common.clean(ad.get("campaign_name"), 140),
			"created_time": lead.get("created_time") or hook.get("created_time"),
		},
	)


def _fetch_meta(channel_doc, hook: dict, form_doc) -> dict:
	"""Gọi Graph API lấy lead + thông tin quảng cáo; lỗi tạm thời -> TransientError (thử lại), còn lại -> ValueError (Manual)."""
	client = MetaClient(channel_doc.name)
	try:
		lead = client.get_lead(hook["lead_id"])
		ad = client.get_ad_info(str(lead.get("ad_id") or hook.get("ad_id") or ""))
	except MetaError as e:
		if e.kind == TRANSIENT:
			raise TransientError(
				_("Graph API lỗi tạm thời, sẽ thử lại")
			) from e  # MetaError đã làm sạch: giữ chuỗi để phân loại
		raise ValueError(f"meta_{e.kind}: {e}") from e
	return meta_form_from_graph(lead, ad, hook, form_doc)


# --- trước resolver -----------------------------------------------------------------------------------------------


def _prelink_facebook_lead(msg: Any, form: dict) -> None:
	"""ĐQ-11: CRM Lead đã có `facebook_lead_id` = mã lead này (do đồng bộ Facebook có sẵn của crm) thì gắn định danh vào Lead đó."""
	if form.get("platform") != "Meta" or not form.get("lead_id"):
		return
	existing = frappe.db.get_value(LEAD, {"facebook_lead_id": form["lead_id"]}, "name")
	if not existing:
		return
	if frappe.db.exists("Omni Identity", {"identity_key": msg.identity_key}):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Omni Identity",
			"identity_key": msg.identity_key,
			"channel_type": msg.channel_type,
			"external_id": msg.identity_key.split(":", 1)[-1],
			"lead": existing,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()


def prepare_batch(msgs: list[Any], channel_doc) -> list[Any]:
	"""Trước resolver: lấy dữ liệu Meta, ánh xạ, đặt hồ sơ người gửi; bỏ tin của biểu mẫu đã tắt khỏi lô."""
	kept: list[Any] = []
	for m in msgs:
		if m.event_type != form_common.EVENT_TYPE:
			kept.append(m)
			continue
		form = form_of(m.name)
		platform = form.get("platform") or form_common.PLATFORM_OF_CHANNEL.get(m.channel_type, "")
		form_doc = get_form(platform, form.get("form_id"))
		if form_doc and not cint(form_doc.enabled):
			continue  # biểu mẫu tắt: chỉ lưu, không tạo Lead (Done, lead trống)
		if platform == "Meta" and not form.get("fetched"):
			form = _fetch_meta(channel_doc, form, form_doc)
		elif not form:
			raise ValueError("form_lead thiếu dữ liệu")
		text = form_common.render_text(form)
		profile = form_common.profile_of(form)
		_save_form(m.name, form, text, profile)
		m.text, m.sender_profile = text, frappe.as_json(profile)
		_prelink_facebook_lead(m, form)
		kept.append(m)
	return kept


# --- chiến dịch ----------------------------------------------------------------------------------------------------


def resolve_campaign(form: dict, form_doc) -> str | None:
	"""Omni Campaign của lead: theo mã chiến dịch nền tảng (tạo nếu chưa có), rồi utm_campaign, rồi chiến dịch mặc định của biểu mẫu."""
	platform, ext = form.get("platform"), form.get("campaign_ext_id")
	if platform in ("Meta", "Google") and ext:
		from omni_crm.omni_crm.doctype.omni_campaign.omni_campaign import campaign_key

		name = frappe.db.get_value(CAMPAIGN, {"campaign_key": campaign_key(platform, ext)}, "name")
		if name:
			return name
		label = (form.get("campaign_name") or f"{platform} {ext}")[:140]
		if frappe.db.exists(CAMPAIGN, label):
			label = f"{label} ({ext})"[:140]
		doc = frappe.get_doc(
			{
				"doctype": CAMPAIGN,
				"campaign_name": label,
				"platform": platform,
				"external_campaign_id": ext,
				"status": "Đang chạy",
			}
		)
		doc.flags.ignore_permissions = True
		try:
			doc.insert()
		except frappe.DuplicateEntryError:
			# V2-08: hai worker cùng thấy chưa có chiến dịch và cùng tạo (lượt lead đầu của chiến dịch mới): dùng bản đã có
			name = frappe.db.get_value(CAMPAIGN, {"campaign_key": campaign_key(platform, ext)}, "name") or (
				label if frappe.db.exists(CAMPAIGN, label) else None
			)
			if not name:
				raise
			return name
		return doc.name
	utm = (form.get("utm") or {}).get("utm_campaign")
	if utm:
		name = frappe.db.get_value(CAMPAIGN, {"utm_campaign": utm}, "name")
		if name:
			return name
	return form_doc.campaign if form_doc and form_doc.campaign else None


def _data(value: Any) -> str | None:
	"""Giá trị cho trường Data (varchar 140) của Lead: cắt 140 ký tự, vì UTM/click id từ người lạ có thể dài hơn (lỗi "Data too long" làm hỏng cả lô)."""
	return (str(value or "").strip()[:DATA_LEN]) or None


def _plain_name(value: Any) -> str:
	"""Tên do khách tự khai dùng làm tên Organization/trường Lead: bỏ `<` `>` (Frappe từ chối tên chứa chúng), gọn khoảng trắng, tối đa 140."""
	return re.sub(r"\s+", " ", re.sub(r"[<>]", " ", str(value or ""))).strip()[:DATA_LEN]


def _attribution_values(form: dict, campaign: str | None, row: Any, created: bool) -> tuple[dict, str | None]:
	"""(giá trị cần ghi vào Lead, tên chiến dịch cũ nếu khách quay lại qua chiến dịch KHÁC)."""
	values: dict[str, Any] = {}
	returned_from: str | None = None
	first = not row.get("omni_campaign") and not row.get("omni_ad_platform")
	if first:
		values["omni_ad_platform"] = form.get("platform")
		if campaign:
			values["omni_campaign"] = campaign
		utm = form.get("utm") or {}
		values.update(
			omni_ad_id=_data(form.get("ad_id")),
			omni_adset_id=_data(form.get("adset_id")),
			omni_form_id=_data(form.get("form_id")),
			omni_click_id=_data(form.get("click_id")),
			omni_landing_page=(form.get("landing_page") or None),
		)
		for key in ("source", "medium", "campaign", "content", "term"):
			values[f"omni_utm_{key}"] = _data(utm.get(f"utm_{key}"))
	elif campaign and row.get("omni_campaign") and campaign != row.get("omni_campaign"):
		returned_from = row.get("omni_campaign")
	if campaign:
		values["omni_last_campaign"] = campaign
	return values, returned_from


# --- Lead: thông tin, Organization, đồng ý ----------------------------------------------------------------------


def _fill_lead(lead: str, mapped: dict, row: Any, created: bool) -> dict:
	"""Điền (không ghi đè) tên đơn vị/chức danh; Lead mới còn được đặt tên từ họ tên biểu mẫu."""
	values: dict[str, Any] = {}
	if _plain_name(mapped.get("job_title")) and not row.get("job_title"):
		values["job_title"] = _plain_name(mapped["job_title"])
	company = _plain_name(mapped.get("company_name"))
	# Tên đơn vị người lạ khai trùng tên một Organization THẬT: không ghi vào `organization` của Lead (trường này là khóa nối
	# tới Organization; ghi vào sẽ gắn nhầm Lead vào nhà thầu khác). Muốn gắn phải qua MST (khớp đúng một, resolver).
	if company and not row.get("organization") and not frappe.db.exists(ORG, company):
		values["organization"] = company
	return values


def _ensure_organization(
	lead: str, mapped: dict, row: Any, matched_by: str, platform: str = ""
) -> tuple[str | None, str | None]:
	"""(Organization để gắn, Organization trùng MST chưa xác minh). MST khai không khớp Organization nào -> tạo `omni_verified = 0`.

	Web Form (ẩn danh) là tín hiệu YẾU: nếu Organization với MST đó đã có thì KHÔNG gắn, trả nó ở phần tử thứ hai để ghi
	chú cho người xử lý. Meta/Google (đã xác thực nền tảng) gắn như cũ (docs/15 mục 7 bước 1).

	Khớp đúng một đã được resolver gắn (matched_by org_*). MST sai định dạng: bỏ qua. Không bao giờ ghi MST rỗng ('' vi phạm UNIQUE).
	"""
	if matched_by.startswith("org_"):
		return None, None
	try:
		tax = normalize_tax_id(mapped.get("tax_id"))
	except InvalidTaxId:
		return None, None
	if not tax:
		return None, None
	existing = frappe.db.get_value(ORG, {"omni_tax_id": tax}, "name")
	if existing:
		return (None, existing) if platform == "Website" else (existing, None)
	company = _plain_name(mapped.get("company_name")) or f"MST {tax}"
	if frappe.db.exists(ORG, company):
		company = f"{company} ({tax})"[:140]
	doc = frappe.get_doc(
		{
			"doctype": ORG,
			"organization_name": company,
			"omni_tax_id": tax,
			"omni_verified": 0,
			"omni_data_source": DATA_SOURCE,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name, None


def record_consent(form: dict, phone: str | None, email: str | None) -> list[str]:
	"""Ghi đồng ý nhận liên hệ lên Contact ĐÃ CÓ khớp SĐT/email (không tạo Contact mới); trả tên Contact đã cập nhật.

	Chỉ nâng "Chưa hỏi" -> "Có"; không bao giờ ghi đè "Từ chối" hay đụng Contact đã xóa dữ liệu. Không có đồng ý thì không ghi gì.
	Chỉ cho Meta/Google (đã xác thực nền tảng); Web Form không bao giờ ghi lên Contact có sẵn.
	"""
	consent = form.get("consent") or {}
	if not consent.get("contact") or form.get("platform") == "Website":
		return []  # Web Form ẩn danh: tích ô đồng ý không đủ giá trị pháp lý để nâng Contact có sẵn; chỉ nằm trên dòng thời gian
	phones = [p for p in [normalize_phone(phone)] if p]
	emails = [e for e in [normalize_email(email)] if e]
	updated: list[str] = []
	for name in prospect_contacts.contacts_matching_any(phones, emails):
		cur = frappe.db.get_value("Contact", name, ["omni_consent", "first_name"], as_dict=True)
		if not cur or cur.omni_consent == "Từ chối" or cur.first_name == prospect_contacts.ERASED:
			continue
		frappe.db.set_value(
			"Contact",
			name,
			{
				"omni_consent": "Có",
				"omni_consent_date": getdate(now_datetime()),
				"omni_consent_method": CONSENT_METHOD.format(
					form.get("platform"), form.get("form_name") or form.get("form_id")
				)[:140],
			},
			update_modified=False,
		)
		updated.append(name)
	return updated


# --- giao sale, báo ------------------------------------------------------------------------------------------------


@contextmanager
def _as_admin():
	"""Job do webhook đẩy chạy bằng Guest mà giao việc (assign_to) cần người dùng thật (ADR 0006)."""
	previous = frappe.session.user
	if previous != "Administrator":
		frappe.set_user("Administrator")
	try:
		yield
	finally:
		if previous != "Administrator":
			frappe.set_user(previous)


def recipients(lead: str) -> list[str]:
	"""Người phụ trách (lead_owner) và người được giao (_assign), đang bật."""
	row = frappe.db.get_value(LEAD, lead, ["lead_owner", "_assign"], as_dict=True) or {}
	users = {row.get("lead_owner")}
	try:
		users.update(json.loads(row.get("_assign") or "[]"))
	except ValueError:
		pass
	return sorted(u for u in users if u and frappe.db.get_value("User", u, "enabled"))


def assign(lead: str, form_doc) -> list[str]:
	"""Giao Lead mới: `default_owner` của biểu mẫu (ghi đè quy tắc), hoặc `assignment_rule` của biểu mẫu; nếu không có thì
	giữ kết quả Assignment Rule tự chạy lúc tạo Lead (G11). Trả danh sách người phụ trách sau cùng."""
	from frappe.desk.form import assign_to

	with _as_admin():
		owner = form_doc.default_owner if form_doc and form_doc.default_owner else None
		if owner and frappe.db.get_value("User", owner, "enabled"):
			if recipients(lead) != [owner]:
				assign_to.clear(LEAD, lead, ignore_permissions=True)
				assign_to.add(
					{
						"assign_to": [owner],
						"doctype": LEAD,
						"name": lead,
						"description": _("Lead từ biểu mẫu quảng cáo"),
					},
					ignore_permissions=True,
				)
			frappe.db.set_value(LEAD, lead, "lead_owner", owner, update_modified=False)
		elif form_doc and form_doc.assignment_rule and not recipients(lead):
			rule = frappe.get_doc("Assignment Rule", form_doc.assignment_rule)
			if not rule.disabled:
				rule.apply_assign(frappe.get_doc(LEAD, lead))
	return recipients(lead)


def unassign(lead: str) -> None:
	"""Lead thử nghiệm không giao sale: bỏ giao việc do Assignment Rule tự chạy lúc tạo và xóa thông báo kèm theo."""
	from frappe.desk.form import assign_to

	with _as_admin():
		assign_to.clear(LEAD, lead, ignore_permissions=True)
	frappe.db.set_value(LEAD, lead, "lead_owner", None, update_modified=False)
	frappe.db.delete("Notification Log", {"document_type": LEAD, "document_name": lead})
	frappe.db.delete(
		"CRM Notification", {"reference_doctype": LEAD, "reference_name": lead}
	)  # chuông của CRM


def notify_users(lead: str, users: list[str], subject: str, content: str) -> int:
	"""Notification Log + realtime cho `users`; trả số người đã báo. Không commit. Không kèm nội dung biểu mẫu."""
	done = 0
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					"email_content": content,
					"document_type": LEAD,
					"document_name": lead,
				}
			).insert(ignore_permissions=True)
			frappe.publish_realtime("omni_form_lead", {"lead": lead}, user=user, after_commit=True)
			done += 1
		except Exception:
			frappe.log_error(title="Omni: không báo được lead quảng cáo", message=f"lead={lead}")
	return done


def _comment(lead: str, text: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": LEAD,
			"reference_name": lead,
			"comment_by": "Omni CRM",
			"content": f"<p>{escape_html(text)}</p>",
		}
	).insert(ignore_permissions=True)


# --- sau resolver -------------------------------------------------------------------------------------------------


def after_resolve(res: Any, msgs: list[Any], channel_doc) -> None:
	"""Sau resolver: ghi quy kết chiến dịch, thông tin Lead, giao sale, báo người phụ trách cho từng tin `form_lead`."""
	if not res.lead:
		return
	for m in msgs:
		if m.event_type == form_common.EVENT_TYPE:
			_apply(res, m)


def _apply(res: Any, msg: Any) -> None:
	form = form_of(msg.name)
	if not form:
		return
	lead = res.lead
	form_doc = get_form(form.get("platform"), form.get("form_id"))
	mapped = form.get("mapped") or {}
	row = frappe.db.get_value(LEAD, lead, ["*"], as_dict=True)
	if form.get("is_test"):
		values: dict[str, Any] = {"omni_is_test": 1, "omni_ad_platform": form.get("platform")}
		if res.created_lead:
			values["omni_form_id"] = form.get("form_id") or None
			frappe.db.set_value(LEAD, lead, values, update_modified=False)
			unassign(lead)
		return
	campaign = resolve_campaign(form, form_doc)
	values, returned_from = _attribution_values(form, campaign, row, res.created_lead)
	values.update(_fill_lead(lead, mapped, row, res.created_lead))
	if form.get("platform") == "Meta":
		if not row.get("facebook_lead_id"):
			values["facebook_lead_id"] = form.get(
				"lead_id"
			)  # chặn đồng bộ Facebook của crm tạo Lead đôi (ĐQ-11)
		if not row.get("facebook_form_id"):
			values["facebook_form_id"] = form.get("form_id") or None
	org, tax_conflict = _ensure_organization(lead, mapped, row, res.matched_by, form.get("platform") or "")
	if tax_conflict:
		_comment(
			lead,
			_(
				"MST khai trên form trùng Organization {0}, chưa xác minh: chưa gắn Lead vào Organization đó"
			).format(tax_conflict),
		)
	if org and (res.created_lead or not row.get("organization")):
		values["organization"] = org
		if res.created_lead and frappe.db.exists("CRM Lead Status", PROSPECT_STATUS):
			values["status"] = PROSPECT_STATUS
	if values:
		frappe.db.set_value(LEAD, lead, values, update_modified=False)
	record_consent(form, mapped.get("phone"), mapped.get("email"))
	if returned_from:
		_comment(lead, _("Quay lại qua chiến dịch {0} (lần đầu qua {1})").format(campaign, returned_from))
	has_contact = bool(normalize_phone(mapped.get("phone")) or normalize_email(mapped.get("email")))
	if not has_contact and res.created_lead:
		_comment(lead, _("Thiếu thông tin liên hệ: biểu mẫu không có SĐT hay email hợp lệ"))
	name = frappe.db.get_value(LEAD, lead, "lead_name") or lead
	unknown_web = form_doc is None and form.get("platform") == "Website"
	if unknown_web and res.created_lead:  # biểu mẫu chưa cấu hình: không giao tự động, để người xử lý quyết
		unassign(lead)
		_comment(
			lead,
			_("Biểu mẫu «{0}» chưa cấu hình Omni Lead Form: chưa giao sale tự động").format(
				form.get("form_id")
			),
		)
	elif res.created_lead:
		users = assign(lead, form_doc)
		notify_users(
			lead,
			users,
			_("Lead mới từ quảng cáo: {0}").format(name),
			_("Lead mới từ biểu mẫu {0}. Hãy liên hệ trong thời hạn phản hồi.").format(
				form.get("form_name") or form.get("form_id") or ""
			),
		)
	elif not recipients(lead) and not form.get("is_test"):
		# V2-08: Lead đã có nhưng CHƯA có người phụ trách (lượt xử lý trước dừng giữa chừng, ví dụ Redis chết sau khi Lead được tạo):
		# giao như Lead mới, nếu không Lead mồ côi không ai được báo
		users = assign(lead, form_doc)
		notify_users(
			lead,
			users,
			_("Lead mới từ quảng cáo: {0}").format(name),
			_("Lead mới từ biểu mẫu {0}. Hãy liên hệ trong thời hạn phản hồi.").format(
				form.get("form_name") or form.get("form_id") or ""
			),
		)
	else:  # khách đã có Lead: giữ người phụ trách và chiến dịch đầu, chỉ báo
		notify_users(
			lead,
			recipients(lead),
			_("Khách {0} vừa điền biểu mẫu quảng cáo").format(name),
			_("Khách đã có Lead này điền thêm một biểu mẫu; xem dòng thời gian."),
		)
