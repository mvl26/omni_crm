"""Nút "Connect Facebook Page" (ADR 0028): state một lần gắn người dùng, đổi code, liệt kê Page không lộ token,
đăng ký webhook giữ trường đang có (leadgen), không đổi Page đã gắn, quyền Omni Admin, sandbox. Không gọi mạng.
Dữ liệu commit thật nên dọn thủ công (tiền tố TD28)."""

from unittest import mock
from urllib.parse import parse_qs, urlsplit

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.api import meta_connect as api
from omni_crm.channels.meta_messenger import connect
from omni_crm.channels.meta_messenger.client import MessengerError

CH = "TD28 messenger"
PAGE, OTHER_PAGE, APP = "571684352685074", "100000000000001", "1012569668539635"
SECRET, CONFIG = "td28-app-secret", "td28-config"


class Resp:
	def __init__(self, status, data):
		self.status_code, self._data = status, data

	def json(self):
		return self._data


def cleanup():
	frappe.db.rollback()
	frappe.set_user("Administrator")
	if frappe.db.exists("Omni Channel", CH):
		frappe.delete_doc("Omni Channel", CH, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()


def make_channel(**kw):
	values = {
		"doctype": "Omni Channel",
		"channel_name": CH,
		"channel_type": "Meta Messenger",
		"enabled": 0,
		"meta_app_id": APP,
		"meta_app_secret": SECRET,
		"meta_login_config_id": CONFIG,
	}
	values.update(kw)
	frappe.get_doc(values).insert(ignore_permissions=True)
	frappe.db.commit()


def state_of(url):
	return parse_qs(urlsplit(url).query)["state"][0]


class Graph:
	"""Giả lập Graph cho `requests.request`: ghi lại mọi lời gọi, trả theo (method, đường dẫn)."""

	def __init__(self, before=None, after=None, pages=None):
		self.calls = []
		self.before = before if before is not None else ["leadgen", "feed"]
		self.after = after
		self.pages = pages or [{"id": PAGE, "name": "Miyano Viet Nam", "access_token": "PAGE-FROM-LIST"}]
		self.subscribed = None

	def __call__(self, method, url, params=None, timeout=None, **kw):
		path = "/" + url.split("/", 4)[4]
		self.calls.append((method, path, dict(params or {})))
		if path == "/me/accounts":
			return Resp(200, {"data": self.pages})
		if path == f"/{PAGE}" and method == "GET":
			return Resp(200, {"id": PAGE, "name": "Miyano Viet Nam", "access_token": "PAGETOK"})
		if path == f"/{PAGE}/subscribed_apps" and method == "POST":
			self.subscribed = params["subscribed_fields"].split(",")
			return Resp(200, {"success": True})
		if path == f"/{PAGE}/subscribed_apps":
			fields = self.before if self.subscribed is None else (self.after or self.subscribed)
			return Resp(
				200,
				{
					"data": [
						{"id": "999", "subscribed_fields": ["x"]},
						{"id": APP, "subscribed_fields": fields},
					]
				},
			)
		return Resp(404, {"error": {"code": 100}})


class TestMetaConnect(FrappeTestCase):
	def setUp(self):
		cleanup()
		self.addCleanup(cleanup)
		make_channel()
		patcher = mock.patch.object(connect.flags, "sandbox", return_value=False)
		patcher.start()
		self.addCleanup(patcher.stop)

	def login(self, code_status=200, code_data=None):
		"""start -> callback; trả (state, session)."""
		state = state_of(connect.start_url(CH))
		data = {"access_token": "USERTOK", "token_type": "bearer"} if code_data is None else code_data
		with mock.patch.object(connect.requests, "get", return_value=Resp(code_status, data)) as get:
			channel, session = connect.complete("CODE", state)
		self.assertEqual(channel, CH)
		self.exchange_params = get.call_args.kwargs["params"]
		return state, session

	def test_start_url_mo_hop_thoai_theo_cau_hinh(self):
		url = connect.start_url(CH)
		parts = urlsplit(url)
		q = {k: v[0] for k, v in parse_qs(parts.query).items()}
		self.assertEqual(parts.netloc, "www.facebook.com")
		self.assertTrue(parts.path.endswith("/dialog/oauth"))
		self.assertEqual((q["client_id"], q["config_id"], q["response_type"]), (APP, CONFIG, "code"))
		self.assertTrue(q["redirect_uri"].endswith("/api/method/omni_crm.api.meta_connect.callback"))
		self.assertNotIn(SECRET, url)
		info = frappe.cache().get_value(connect._state_key(q["state"]), expires=True)
		self.assertEqual(info, {"channel": CH, "user": "Administrator"})

	def test_thieu_cau_hinh_bao_dung_o(self):
		frappe.db.set_value("Omni Channel", CH, "meta_login_config_id", "")
		with self.assertRaises(frappe.ValidationError) as cm:
			connect.start_url(CH)
		self.assertIn("Facebook Login configuration ID", str(cm.exception))

	def test_kenh_khac_loai_bi_tu_choi(self):
		frappe.db.set_value("Omni Channel", CH, "channel_type", "Meta Lead Ads")
		with self.assertRaises(frappe.ValidationError):
			connect.start_url(CH)

	def test_sandbox_khong_mo_hop_thoai(self):
		with (
			mock.patch.object(connect.flags, "sandbox", return_value=True),
			self.assertRaises(frappe.ValidationError),
		):
			connect.start_url(CH)

	def test_chi_omni_admin(self):
		with mock.patch.object(connect.frappe, "get_roles", return_value=["System Manager"]):
			for call in (
				lambda: connect.start_url(CH),
				lambda: connect.complete("CODE", "x"),
				lambda: connect.list_pages("x"),
				lambda: connect.subscribe("x", PAGE),
			):
				with self.assertRaises(frappe.PermissionError):
					call()

	def test_doi_code_lay_token_va_state_chi_dung_mot_lan(self):
		state, session = self.login()
		self.assertEqual(self.exchange_params["client_secret"], SECRET)
		self.assertEqual(self.exchange_params["code"], "CODE")
		info = frappe.cache().get_value(connect._session_key(session), expires=True)
		self.assertEqual((info["channel"], info["token"]), (CH, "USERTOK"))
		with self.assertRaises(frappe.PermissionError):
			connect.complete("CODE", state)

	def test_state_cua_nguoi_khac_bi_tu_choi(self):
		state = state_of(connect.start_url(CH))
		with (
			mock.patch.dict(frappe.session, {"user": "td28.other@example.com"}),
			mock.patch.object(connect.frappe, "get_roles", return_value=["Omni Admin"]),
			mock.patch.object(connect.requests, "get") as get,
			self.assertRaises(frappe.PermissionError),
		):
			connect.complete("CODE", state)
		get.assert_not_called()

	def test_meta_tu_choi_code_khong_lo_bi_mat(self):
		state = state_of(connect.start_url(CH))
		bad = Resp(400, {"error": {"code": 100, "message": f"bad code for {SECRET}"}})
		with (
			mock.patch.object(connect.requests, "get", return_value=bad),
			self.assertRaises(MessengerError) as cm,
		):
			connect.complete("CODE", state)
		self.assertIn("100", cm.exception.message_vi)
		self.assertNotIn(SECRET, cm.exception.message_vi)

	def test_huy_hop_thoai(self):
		state = state_of(connect.start_url(CH))
		self.assertEqual(connect.cancel(state), CH)
		self.assertIsNone(connect.cancel(state))
		self.assertIsNone(connect.cancel(None))

	def test_danh_sach_page_khong_tra_token(self):
		_state, session = self.login()
		graph = Graph()
		with mock.patch("requests.request", side_effect=graph):
			out = connect.list_pages(session)
		self.assertEqual(out["pages"], [{"id": PAGE, "name": "Miyano Viet Nam"}])
		self.assertNotIn("PAGE-FROM-LIST", str(out))
		self.assertEqual(graph.calls[0][:2], ("GET", "/me/accounts"))
		self.assertEqual(graph.calls[0][2]["access_token"], "USERTOK")

	def test_dang_ky_giu_truong_dang_co_va_luu_ket_qua(self):
		_state, session = self.login()
		graph = Graph(before=["leadgen", "feed"])
		with mock.patch("requests.request", side_effect=graph):
			out = connect.subscribe(session, PAGE)
		post = [c for c in graph.calls if c[0] == "POST"]
		self.assertEqual(len(post), 1)
		self.assertEqual(post[0][2]["access_token"], "PAGETOK")
		self.assertEqual(graph.subscribed[:2], ["leadgen", "feed"])
		self.assertTrue(set(connect.PAGE_FIELDS) <= set(graph.subscribed))
		self.assertEqual(len(graph.subscribed), len(set(graph.subscribed)))
		self.assertEqual((out["page_id"], out["page_name"], out["missing"]), (PAGE, "Miyano Viet Nam", []))
		doc = frappe.get_doc("Omni Channel", CH)
		self.assertEqual(
			(doc.meta_page_id, doc.meta_page_name, doc.meta_connected_by),
			(PAGE, "Miyano Viet Nam", "Administrator"),
		)
		self.assertIn("leadgen", doc.meta_subscribed_fields)
		self.assertTrue(doc.meta_connected_at)
		# token Page của luồng này không thay token System User đang vận hành
		self.assertFalse(doc.get_password("meta_page_access_token", raise_exception=False))
		# phiên đã dùng xong
		self.assertIsNone(frappe.cache().get_value(connect._session_key(session), expires=True))

	def test_bao_truong_meta_khong_nhan(self):
		_state, session = self.login()
		graph = Graph(before=[], after=["messages"])
		with mock.patch("requests.request", side_effect=graph):
			out = connect.subscribe(session, PAGE)
		self.assertIn("leadgen", out["missing"])
		self.assertEqual(out["fields"], ["messages"])

	def test_khong_doi_sang_page_khac(self):
		frappe.db.set_value("Omni Channel", CH, "meta_page_id", PAGE)
		_state, session = self.login()
		graph = Graph()
		with mock.patch("requests.request", side_effect=graph), self.assertRaises(frappe.ValidationError):
			connect.subscribe(session, OTHER_PAGE)
		self.assertEqual(graph.calls, [])

	def test_page_id_sai_dang(self):
		_state, session = self.login()
		with self.assertRaises(frappe.ValidationError):
			connect.subscribe(session, "1/../me")

	def test_api_callback_chuyen_ve_form(self):
		state = state_of(connect.start_url(CH))
		with mock.patch.object(connect.requests, "get", return_value=Resp(200, {"access_token": "USERTOK"})):
			api.callback(code="CODE", state=state)
		location = frappe.local.response["location"]
		self.assertIn("/app/omni-channel/TD28%20messenger?fb_connect=", location)
		self.assertNotIn("USERTOK", location)

	def test_ghi_de_url_callback_qua_site_config(self):
		fixed = "https://yhct.miyano.com.vn/api/method/omni_crm.api.meta_connect.callback"
		with mock.patch.dict(frappe.conf, {connect.REDIRECT_CONF: fixed}):
			q = parse_qs(urlsplit(connect.start_url(CH)).query)
			self.assertEqual(q["redirect_uri"], [fixed])
			_state, _session = self.login()
		self.assertEqual(self.exchange_params["redirect_uri"], fixed)

	def test_api_callback_khi_huy(self):
		state = state_of(connect.start_url(CH))
		api.callback(state=state, error="access_denied", error_reason="user_denied")
		self.assertTrue(frappe.local.response["location"].endswith("?fb_connect_error=access_denied"))
		with self.assertRaises(frappe.PermissionError):
			api.callback(state=state, error="access_denied")
