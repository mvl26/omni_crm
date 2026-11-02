"""Client đọc lead từ Graph API (CHỈ ĐỌC): timeout 10 giây, thử lại có giới hạn khi lỗi mạng/5xx/429.

Token Page nằm trong header Authorization (không bao giờ trong URL/query) và không xuất hiện trong log hay lỗi:
mọi `requests.RequestException` được đổi thành MetaError với thông điệp cố định. Lấy lead là chiều VÀO, không phải
gửi tin cho khách nên không bị chặn bởi sandbox_mode (không có token thì không gọi).
CHƯA THỬ VỚI NỀN TẢNG THẬT: tên trường theo tài liệu Meta (kiểm ngày 2026-11-02, docs/12 G15).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import frappe
import requests
from frappe import _

from omni_crm.channels.meta_leads.errors import PERMANENT, TRANSIENT, MetaError, to_error

API_BASE = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"  # tài liệu Meta ngày 2026-11-02; đổi bằng site_config `omni_meta_graph_version`
TIMEOUT = 10
RETRIES = 2
LEAD_FIELDS = "id,created_time,ad_id,form_id,field_data"
PAGE_LIMIT = 100
MAX_PAGES = 10
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


def graph_version() -> str:
	"""Phiên bản Graph API (site_config có thể ghi đè, chỉ nhận dạng vNN.N)."""
	import re

	version = str(frappe.conf.get("omni_meta_graph_version") or DEFAULT_VERSION)
	return version if re.fullmatch(r"v\d{1,3}\.\d{1,2}", version) else DEFAULT_VERSION


def _check_id(value: str) -> str:
	"""Mã lead/quảng cáo/biểu mẫu lấy từ payload chỉ gồm chữ, số, `_`, `-`: không cho `/`, `..`, `?`, `#` đổi đường dẫn Graph."""
	if not _SAFE_ID.fullmatch(str(value or "")):
		raise MetaError(PERMANENT, _("Mã Meta không hợp lệ"))
	return str(value)


class MetaClient:
	"""Gọi Graph API cho một Omni Channel `Meta Lead Ads`."""

	def __init__(self, channel: str):
		self.channel = channel

	def _token(self) -> str:
		doc = frappe.get_doc("Omni Channel", self.channel)
		token = doc.get_password("meta_page_access_token", raise_exception=False)
		if not token:
			raise MetaError(PERMANENT, _("Kênh Meta chưa có Page access token"))
		return token

	def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
		token = self._token()
		url = f"{API_BASE}/{graph_version()}/{path.lstrip('/')}"
		attempt = 0
		while True:
			try:
				resp = requests.get(
					url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
				)
				payload: Any = resp.json()
			except requests.RequestException:
				if attempt < RETRIES:
					attempt += 1
					time.sleep(min(attempt, 2))
					continue
				raise MetaError(TRANSIENT, _("Không kết nối được Graph API")) from None
			except ValueError:
				raise to_error(resp.status_code, None) from None
			err = payload.get("error") if isinstance(payload, dict) else None
			if resp.status_code >= 400 or err:
				error = to_error(resp.status_code, (err or {}).get("code") if isinstance(err, dict) else None)
				if error.kind == TRANSIENT and attempt < RETRIES:
					attempt += 1
					time.sleep(min(attempt, 2))
					continue
				raise error
			return payload if isinstance(payload, dict) else {}

	def get_lead(self, leadgen_id: str) -> dict[str, Any]:
		"""Lead theo leadgen_id: {id, created_time, ad_id, form_id, field_data, custom_disclaimer_responses?}."""
		leadgen_id = _check_id(leadgen_id)
		lead = self._get(leadgen_id, {"fields": LEAD_FIELDS})
		try:  # ô đồng ý tùy chỉnh không nằm trong field_data (tài liệu Meta): lấy riêng, lỗi không làm hỏng lead
			extra = self._get(leadgen_id, {"fields": "custom_disclaimer_responses"})
			lead["custom_disclaimer_responses"] = extra.get("custom_disclaimer_responses") or []
		except MetaError as e:
			frappe.logger("omni").warning("omni: meta disclaimer loi (%s)", e.kind)
		return lead

	def get_ad_info(self, ad_id: str) -> dict[str, str]:
		"""{campaign_id, campaign_name, adset_id, adset_name, ad_name}; cache theo ngày, lỗi thì trả {}."""
		if not ad_id:
			return {}
		if not _SAFE_ID.fullmatch(str(ad_id)):
			return {}  # mã quảng cáo lạ: bỏ qua thông tin quảng cáo, không làm hỏng lead
		cache = frappe.cache()
		key = cache.make_key(f"omni:meta:ad:{ad_id}:{frappe.utils.today()}")
		hit = cache.get(key)
		if hit:
			return json.loads(hit)
		try:
			data = self._get(ad_id, {"fields": "name,adset_id,campaign_id,adset{name},campaign{name}"})
		except MetaError as e:
			frappe.logger("omni").warning("omni: meta ad info loi (%s)", e.kind)
			return {}
		info = {
			"ad_name": str(data.get("name") or ""),
			"adset_id": str(data.get("adset_id") or ""),
			"adset_name": str((data.get("adset") or {}).get("name") or ""),
			"campaign_id": str(data.get("campaign_id") or ""),
			"campaign_name": str((data.get("campaign") or {}).get("name") or ""),
		}
		cache.set(key, json.dumps(info), ex=86400)
		return info

	def list_form_leads(self, form_id: str, since_ts: int | None) -> list[dict[str, Any]]:
		"""Lead của biểu mẫu có time_created > since_ts (Unix), tối đa MAX_PAGES trang (đối soát hằng giờ)."""
		params: dict[str, Any] = {"fields": LEAD_FIELDS, "limit": PAGE_LIMIT}
		if since_ts:
			params["filtering"] = json.dumps(
				[{"field": "time_created", "operator": "GREATER_THAN", "value": int(since_ts)}]
			)
		leads: list[dict[str, Any]] = []
		path = f"{_check_id(form_id)}/leads"
		for _page in range(MAX_PAGES):
			data = self._get(path, params)
			leads += [x for x in data.get("data") or [] if isinstance(x, dict)]
			after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
			if not (data.get("paging") or {}).get("next") or not after:
				break
			params = {**params, "after": after}
		return leads
