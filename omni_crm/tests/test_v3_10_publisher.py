"""V3-10 Prompt 2: bộ đăng bài theo lịch. Giả lập HTTP / adapter, không gọi mạng thật.

Bộ đăng bài commit sau mỗi bước (job nền), nên test tự dọn dữ liệu theo tiền tố.
"""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from omni_crm.marketing import approval, channels, roles, rules
from omni_crm.marketing.publisher import content, dispatch, facebook, run, zalo
from omni_crm.marketing.publisher.base import PublishResult
from omni_crm.marketing.publisher.errors import ProviderError, TerminalError

PREFIX = "TM320"
ITEM = "Omni Content Item"
TARGET = "Omni Content Target"
SETTINGS = "Omni Marketing Settings"
PAGE_TOKEN = "tm320-page-token-khong-that-" + "p" * 24


def cleanup() -> None:
	frappe.db.rollback()
	camps = frappe.get_all("Omni Campaign", {"campaign_name": ["like", f"{PREFIX}%"]}, pluck="name")
	items = frappe.get_all(ITEM, {"campaign": ["in", camps or [""]]}, pluck="name")
	targets = frappe.get_all(TARGET, {"content_item": ["in", items or [""]]}, pluck="name")
	frappe.db.delete("Omni Publish Log", {"target": ["in", targets or [""]]})
	frappe.db.delete(TARGET, {"name": ["in", targets or [""]]})
	frappe.db.delete(ITEM, {"name": ["in", items or [""]]})
	frappe.db.delete("Omni Campaign", {"name": ["in", camps or [""]]})
	frappe.db.delete("Notification Log", {"subject": ["like", f"%{PREFIX}%"]})
	frappe.db.delete("Omni Channel", {"channel_name": ["like", f"{PREFIX}%"]})
	frappe.db.delete("__Auth", {"doctype": "Omni Channel", "name": ["like", f"{PREFIX}%"]})
	frappe.db.sql("delete from tabSingles where doctype=%s", SETTINGS)
	frappe.db.commit()
	frappe.db.value_cache.pop(SETTINGS, None)


def set_setting(field: str, value) -> None:
	"""tabSingles không có khóa duy nhất (doctype, field): xóa rồi ghi, không dùng ON DUPLICATE KEY."""
	frappe.db.sql("delete from tabSingles where doctype=%s and field=%s", (SETTINGS, field))
	frappe.db.sql(
		"insert into tabSingles (doctype, field, value) values (%s, %s, %s)", (SETTINGS, field, value)
	)
	frappe.db.commit()
	frappe.db.value_cache.pop(SETTINGS, None)


class FakeAdapter:
	"""Adapter giả: lần lượt trả/ném theo `script`; ghi lại số lần publish/reconcile."""

	def __init__(self, script=None, reconcile_result=None):
		self.script = list(script or [])
		self.reconcile_result = reconcile_result
		self.published = 0
		self.reconciled = 0

	def publish(self, payload):
		self.published += 1
		step = self.script.pop(0) if self.script else PublishResult(f"post-{self.published}")
		if isinstance(step, Exception):
			raise step
		return step

	def reconcile(self, payload, since_minutes=30):
		self.reconciled += 1
		return self.reconcile_result

	def fetch_permalink(self, payload, external_post_id):
		return f"https://facebook.com/{external_post_id}"


class PublisherCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		roles.ensure_roles()
		cleanup()

	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		self.zalo = (
			frappe.get_doc(
				{
					"doctype": "Omni Channel",
					"channel_name": f"{PREFIX}-zalo",
					"channel_type": "Zalo OA",
					"zalo_app_id": "app-1",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.meta = (
			frappe.get_doc(
				{
					"doctype": "Omni Channel",
					"channel_name": f"{PREFIX}-meta",
					"channel_type": "Meta Lead Ads",
					"meta_page_id": "320320320",
					"meta_verify_token": "v",
					"meta_page_access_token": PAGE_TOKEN,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.commit()
		self.enqueue = mock.patch("frappe.enqueue").start()
		self.addCleanup(mock.patch.stopall)
		self.addCleanup(cleanup)

	def approved_item(
		self, platforms=(rules.FACEBOOK,), fmt=None, caption="Miyano giới thiệu SPD", media=None
	) -> tuple[str, list[str]]:
		camp = frappe.get_doc(
			{
				"doctype": "Omni Campaign",
				"campaign_name": f"{PREFIX} SPD {frappe.generate_hash(length=6)}",
				"platform": "Đa kênh",
				"status": "Đang chạy",
			}
		).insert(ignore_permissions=True)
		item = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": camp.name,
				"title": "Bài thử",
				"caption": caption,
				"media": media,
				"quality_score": 95,
			}
		)
		item.flags.score_via_ai = True  # điểm chỉ do bộ chấm AI ghi (Prompt 4)
		item.insert(ignore_permissions=True)
		names = []
		for p in platforms:
			names.append(
				frappe.get_doc(
					{
						"doctype": TARGET,
						"content_item": item.name,
						"platform": p,
						"channel_account": {rules.FACEBOOK: self.meta, rules.ZALO: self.zalo}.get(p),
						"post_format": fmt or {rules.FACEBOOK: "fb_link", rules.ZALO: "zalo_article"}[p],
						"scheduled_at": add_to_date(now_datetime(), minutes=1),
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		approval.submit(item.name)
		approval.approve(item.name)
		for n in names:  # đến giờ
			frappe.db.set_value(TARGET, n, "scheduled_at", add_to_date(now_datetime(), minutes=-1))
		frappe.db.commit()
		return item.name, names

	def run_with(self, adapter, name):
		with mock.patch.object(run, "get_adapter", return_value=adapter):
			return run.publish_target(name)

	def claim(self, name):
		"""Như dispatch: đến giờ thử lại thì xóa next_retry_at."""
		frappe.db.set_value(TARGET, name, "next_retry_at", None)
		frappe.db.commit()


class TestDispatch(PublisherCase):
	def test_picks_due_approved_only(self):
		_item, [due] = self.approved_item()
		# bài chưa duyệt / đã hủy / chưa đến giờ không được lấy
		_i2, [pending] = self.approved_item()
		frappe.db.set_value(TARGET, pending, "scheduled_at", add_to_date(now_datetime(), minutes=30))
		i3, [cancelled] = self.approved_item()
		approval.cancel(i3)
		draft = frappe.get_doc(
			{
				"doctype": ITEM,
				"campaign": frappe.db.get_value(ITEM, i3, "campaign"),
				"title": "nháp",
				"caption": "x",
			}
		).insert(ignore_permissions=True)
		review = frappe.get_doc(
			{
				"doctype": TARGET,
				"content_item": draft.name,
				"platform": rules.FACEBOOK,
				"channel_account": self.meta,
				"post_format": "fb_link",
				"scheduled_at": add_to_date(now_datetime(), minutes=-1),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		names = [r.name for r in dispatch.due_targets()["scheduled"]]
		self.assertIn(due, names)
		self.assertNotIn(pending, names)
		self.assertNotIn(cancelled, names)
		self.assertNotIn(review.name, names)

	def test_two_dispatches_enqueue_once(self):
		_item, [name] = self.approved_item()
		first = dispatch.dispatch_due()
		second = dispatch.dispatch_due()
		self.assertEqual(first["enqueued"], 1)
		self.assertEqual(second["enqueued"], 0)
		calls = [c for c in self.enqueue.call_args_list if c.kwargs.get("name") == name]
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].kwargs["job_id"], f"oc-publish-{name}")
		self.assertEqual(calls[0].kwargs["queue"], "long")
		self.assertEqual(frappe.db.get_value(TARGET, name, "status"), rules.T_PROCESSING)

	def test_claim_is_atomic(self):
		_item, [name] = self.approved_item()
		self.assertTrue(dispatch._claim_scheduled(name))
		self.assertFalse(dispatch._claim_scheduled(name))  # lượt chạy chồng thứ hai: 0 dòng bị đổi

	def test_late_window_fails_with_message(self):
		_item, [name] = self.approved_item()
		frappe.db.set_value(TARGET, name, "scheduled_at", add_to_date(now_datetime(), minutes=-31))
		frappe.db.commit()
		out = dispatch.dispatch_due()
		self.assertEqual(out["late"], 1)
		row = frappe.db.get_value(TARGET, name, ["status", "error_type", "last_error"], as_dict=True)
		self.assertEqual(row.status, rules.T_FAILED)
		self.assertEqual(row.error_type, "terminal")
		self.assertEqual(row.last_error, "Đã quá cửa sổ cho phép đăng muộn")
		self.assertTrue(frappe.db.exists("Omni Publish Log", {"target": name, "outcome": "error"}))
		self.enqueue.assert_not_called()

	def test_hub_disabled_does_nothing(self):
		_item, [name] = self.approved_item()
		set_setting("hub_disabled", "1")
		self.assertEqual(dispatch.dispatch_due()["enqueued"], 0)
		self.assertEqual(frappe.db.get_value(TARGET, name, "status"), rules.T_SCHEDULED)

	def test_stuck_processing_is_retried_with_reconcile(self):
		_item, [name] = self.approved_item()
		set_setting("dry_run", "0")
		dispatch._claim_scheduled(name)
		frappe.db.set_value(TARGET, name, "attempts", 1, update_modified=False)
		frappe.db.sql(
			"update `tabOmni Content Target` set modified=%s where name=%s",
			(add_to_date(now_datetime(), minutes=-20), name),
		)
		frappe.db.commit()
		out = dispatch.dispatch_due()
		self.assertEqual(out["stuck"], 1)
		self.assertEqual(out["enqueued"], 1)
		fake = FakeAdapter(reconcile_result=PublishResult("found-1", "https://fb/x"))
		with mock.patch("omni_crm.flags.sandbox", return_value=False):
			self.assertEqual(self.run_with(fake, name), rules.T_SUCCESS)
		self.assertEqual(fake.reconciled, 1)
		self.assertEqual(fake.published, 0)


class TestPublish(PublisherCase):
	def setUp(self):
		super().setUp()
		set_setting("dry_run", "0")
		self.sandbox = mock.patch("omni_crm.flags.sandbox", return_value=False).start()

	def _start(self, name):
		dispatch._claim_scheduled(name)
		frappe.db.commit()

	def test_dry_run_never_calls_network(self):
		set_setting("dry_run", "1")
		item, [name] = self.approved_item()
		self._start(name)
		with (
			mock.patch("requests.request") as req,
			mock.patch("requests.get") as get,
			mock.patch("requests.post") as post,
		):
			status = run.publish_target(name)
		self.assertEqual(status, rules.T_SUCCESS, frappe.db.get_value(TARGET, name, "last_error"))
		req.assert_not_called()
		get.assert_not_called()
		post.assert_not_called()
		self.assertEqual(frappe.db.get_value(TARGET, name, "external_post_id"), f"dryrun-{name}")
		self.assertTrue(frappe.db.exists("Omni Publish Log", {"target": name, "outcome": "dry_run"}))
		self.assertEqual(frappe.db.get_value(ITEM, item, "status"), rules.I_PUBLISHED)

	def test_sandbox_counts_as_dry_run(self):
		self.sandbox.return_value = True
		_item, [name] = self.approved_item()
		self._start(name)
		with mock.patch("requests.request") as req:
			run.publish_target(name)
		req.assert_not_called()

	def test_success_saves_ids_and_realtime(self):
		_item, [name] = self.approved_item()
		self._start(name)
		with mock.patch("frappe.publish_realtime") as rt:
			self.assertEqual(self.run_with(FakeAdapter(), name), rules.T_SUCCESS)
		row = frappe.db.get_value(
			TARGET, name, ["external_post_id", "permalink", "published_at"], as_dict=True
		)
		self.assertEqual(row.external_post_id, "post-1")
		self.assertEqual(row.permalink, "https://facebook.com/post-1")
		self.assertTrue(row.published_at)
		self.assertEqual(rt.call_args.args[0], "oc_publish_update")
		self.assertTrue(rt.call_args.kwargs.get("after_commit"))

	def test_provider_error_retries_three_times_then_fails(self):
		_item, [name] = self.approved_item()
		self._start(name)
		fake = FakeAdapter(script=[ProviderError("Facebook lỗi tạm thời")] * 4)
		gaps = []
		for i in range(4):
			before = now_datetime()
			status = self.run_with(fake, name)
			nxt = frappe.db.get_value(TARGET, name, "next_retry_at")
			if i < 3:
				self.assertEqual(status, rules.T_PROCESSING)
				gaps.append(round((nxt - before).total_seconds() / 60))
				self.claim(name)
			else:
				self.assertEqual(status, rules.T_FAILED)
				self.assertIsNone(nxt)
		self.assertEqual(gaps, [1, 5, 15])
		self.assertEqual(fake.published, 4)
		self.assertEqual(frappe.db.get_value(TARGET, name, "attempts"), 4)
		self.assertEqual(frappe.db.count("Omni Publish Log", {"target": name}), 4)
		self.assertEqual(frappe.db.get_value(TARGET, name, "error_type"), "provider")

	def test_terminal_error_no_retry_and_channel_needs_reconnect(self):
		_item, [name] = self.approved_item()
		self._start(name)
		fake = FakeAdapter(script=[TerminalError("Facebook từ chối: mã truy cập Trang hết hạn")])
		self.assertEqual(self.run_with(fake, name), rules.T_FAILED)
		self.assertEqual(fake.published, 1)
		self.assertEqual(frappe.db.get_value(TARGET, name, "error_type"), "terminal")
		self.assertEqual(channels._checks()[self.meta]["status"], channels.RECONNECT)
		if frappe.get_all("Has Role", {"role": "Omni Admin", "parenttype": "User"}, limit=1):
			self.assertTrue(frappe.db.exists("Notification Log", {"subject": ["like", f"%{PREFIX}-meta%"]}))

	def test_timeout_then_reconcile_finds_post_no_second_publish(self):
		_item, [name] = self.approved_item()
		self._start(name)
		fake = FakeAdapter(
			script=[ProviderError("Facebook không trả lời kịp", ambiguous=True)],
			reconcile_result=PublishResult("320_999", "https://facebook.com/320_999"),
		)
		self.assertEqual(self.run_with(fake, name), rules.T_PROCESSING)
		self.claim(name)
		self.assertEqual(self.run_with(fake, name), rules.T_SUCCESS)
		self.assertEqual(fake.published, 1)
		self.assertEqual(fake.reconciled, 1)
		self.assertEqual(frappe.db.get_value(TARGET, name, "external_post_id"), "320_999")

	def test_validation_error_empty_caption(self):
		_item, [name] = self.approved_item(caption=" ")
		self._start(name)
		fake = FakeAdapter()
		self.assertEqual(self.run_with(fake, name), rules.T_FAILED)
		self.assertEqual(fake.published, 0)
		self.assertEqual(frappe.db.get_value(TARGET, name, "error_type"), "validation")

	def test_zalo_without_cover_is_validation_error(self):
		# duyệt đòi ảnh bìa; ảnh bị gỡ sau khi duyệt thì bộ đăng bài vẫn chặn (lớp kiểm thứ hai)
		item, [name] = self.approved_item(platforms=(rules.ZALO,), media="/files/tm320-cover.png")
		frappe.db.set_value(ITEM, item, "media", "")
		frappe.db.commit()
		self._start(name)
		self.assertEqual(self.run_with(FakeAdapter(), name), rules.T_FAILED)
		self.assertIn("ảnh bìa", frappe.db.get_value(TARGET, name, "last_error"))

	def test_item_status_aggregates(self):
		item, names = self.approved_item(platforms=(rules.FACEBOOK, rules.FACEBOOK))
		for n in names:
			self._start(n)
		self.run_with(FakeAdapter(), names[0])
		self.assertEqual(frappe.db.get_value(ITEM, item, "status"), rules.I_PROCESSING)
		self.run_with(FakeAdapter(script=[TerminalError("thiếu quyền")]), names[1])
		self.assertEqual(frappe.db.get_value(ITEM, item, "status"), rules.I_PARTIAL)

	def test_item_all_failed(self):
		item, names = self.approved_item(platforms=(rules.FACEBOOK, rules.FACEBOOK))
		for n in names:
			self._start(n)
			self.run_with(FakeAdapter(script=[TerminalError("thiếu quyền")]), n)
		self.assertEqual(frappe.db.get_value(ITEM, item, "status"), rules.I_FAILED)

	def test_not_processing_is_ignored(self):
		_item, [name] = self.approved_item()  # chưa được dispatch khóa
		fake = FakeAdapter()
		self.assertEqual(self.run_with(fake, name), rules.T_SCHEDULED)
		self.assertEqual(fake.published, 0)


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._body = status, body

	def json(self):
		return self._body


def fb_payload(fmt="fb_link", images=()):
	return content.Payload(
		target="t1",
		platform=rules.FACEBOOK,
		post_format=fmt,
		channel=None,
		title="T",
		caption="Xin chào",
		tracking_url="https://yhct.miyano.com.vn/spd/?utm_source=facebook&utm_medium=social&utm_campaign=c&utm_content=bai-01",
		images=list(images),
	)


class TestFacebookAdapter(FrappeTestCase):
	def setUp(self):
		self.ad = facebook.FacebookAdapter()
		p = mock.patch.object(facebook.FacebookAdapter, "_page", return_value=("320320", "tok"))
		p.start()
		self.addCleanup(mock.patch.stopall)

	def test_link_post(self):
		with mock.patch("requests.request", return_value=FakeResp(200, {"id": "320_1"})) as req:
			res = self.ad.publish(fb_payload())
		self.assertEqual(res.external_post_id, "320_1")
		args, kwargs = req.call_args
		self.assertEqual(args[0], "POST")
		self.assertTrue(args[1].endswith("/320320/feed"))
		self.assertEqual(kwargs["data"]["link"], fb_payload().tracking_url)
		self.assertIn("utm_content=bai-01", kwargs["data"]["message"])
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
		self.assertEqual(kwargs["timeout"], facebook.TIMEOUT)

	def test_photo_post_uses_caption(self):
		with mock.patch(
			"requests.request", return_value=FakeResp(200, {"id": "ph1", "post_id": "320_2"})
		) as req:
			res = self.ad.publish(fb_payload("fb_photo", ["https://x/a.jpg"]))
		self.assertEqual(res.external_post_id, "320_2")
		self.assertTrue(req.call_args.args[1].endswith("/320320/photos"))
		self.assertIn("caption", req.call_args.kwargs["data"])

	def test_multi_photo_attached_media(self):
		resps = [FakeResp(200, {"id": "ph1"}), FakeResp(200, {"id": "ph2"}), FakeResp(200, {"id": "320_3"})]
		with mock.patch("requests.request", side_effect=resps) as req:
			res = self.ad.publish(fb_payload("fb_multi_photo", ["https://x/a.jpg", "https://x/b.jpg"]))
		self.assertEqual(res.external_post_id, "320_3")
		self.assertEqual(req.call_args_list[0].kwargs["data"]["published"], "false")
		feed = req.call_args_list[2].kwargs["data"]
		self.assertEqual(feed["attached_media[0]"], '{"media_fbid": "ph1"}')
		self.assertEqual(feed["attached_media[1]"], '{"media_fbid": "ph2"}')

	def test_error_classification(self):
		self.assertIsInstance(facebook.classify(500, {}), ProviderError)
		self.assertIsInstance(facebook.classify(400, {"error": {"code": 190}}), TerminalError)
		self.assertIsInstance(facebook.classify(403, {"error": {"code": 200}}), TerminalError)
		self.assertEqual(facebook.classify(400, {"error": {"code": 100}}).kind, "validation")
		self.assertIsInstance(facebook.classify(400, {"error": {"code": 368}}), ProviderError)

	def test_timeout_is_ambiguous(self):
		import requests

		with mock.patch("requests.request", side_effect=requests.Timeout()):
			with self.assertRaises(ProviderError) as ctx:
				self.ad.publish(fb_payload())
		self.assertTrue(ctx.exception.ambiguous)

	def test_reconcile_matches_tracking_query(self):
		posts = {
			"data": [
				{"id": "320_9", "message": "Xin chào\n\n" + fb_payload().tracking_url, "permalink_url": "p"}
			]
		}
		with mock.patch("requests.request", return_value=FakeResp(200, posts)):
			self.assertEqual(self.ad.reconcile(fb_payload()).external_post_id, "320_9")
		with mock.patch(
			"requests.request", return_value=FakeResp(200, {"data": [{"id": "x", "message": "khác"}]})
		):
			self.assertIsNone(self.ad.reconcile(fb_payload()))


class FakeZaloClient:
	def __init__(self, replies):
		self.replies, self.calls = list(replies), []

	def _request(self, method, path, **kw):
		self.calls.append((method, path, kw))
		return self.replies.pop(0)


class TestZaloAdapter(FrappeTestCase):
	def payload(self, title="Tiêu đề"):
		return content.Payload(
			target="t1",
			platform=rules.ZALO,
			post_format="zalo_article",
			channel="ch",
			title=title,
			caption="Nội dung " * 60,
			tracking_url="https://yhct.miyano.com.vn/spd/?utm_source=zalo",
			images=["https://x/cover.jpg", "https://x/2.jpg"],
		)

	def test_article_body_limits_and_tracking(self):
		body = zalo.article_body(self.payload())
		self.assertEqual(body["cover"]["photo_url"], "https://x/cover.jpg")
		self.assertLessEqual(len(body["description"]), 300)
		self.assertIn("utm_source=zalo", body["body"][0]["content"])
		self.assertEqual(body["body"][1], {"type": "image", "url": "https://x/2.jpg"})
		self.assertEqual(body["tracking_link"], "https://yhct.miyano.com.vn/spd/?utm_source=zalo")

	def test_poll_until_article_id(self):
		fake = FakeZaloClient([{"token": "tk"}, {"status": "processing"}, {"article_id": "art-1"}])
		with mock.patch.object(zalo, "_client", return_value=fake):
			res = zalo.ZaloAdapter().publish(self.payload(), sleep=lambda s: None)
		self.assertEqual(res.external_post_id, "art-1")
		self.assertEqual(fake.calls[1][2]["body"], {"token": "tk"})

	def test_rejected_article_keeps_zalo_code_and_message(self):
		from omni_crm.channels.zalo import errors as zerr
		from omni_crm.marketing.publisher.errors import PublishValidationError

		class Rejecting(FakeZaloClient):
			def _request(self, method, path, **kw):
				self.calls.append((method, path, kw))
				raise zerr.to_error(-201, 200, "cover photo_url is invalid")

		with (
			mock.patch.object(zalo, "_client", return_value=Rejecting([])),
			mock.patch.object(zalo.frappe, "log_error") as log,
		):
			with self.assertRaises(PublishValidationError) as ctx:
				zalo.ZaloAdapter().publish(self.payload(), sleep=lambda s: None)
		msg = ctx.exception.message
		self.assertIn("-201", msg)
		self.assertIn("tạo bài", msg)
		self.assertIn("cover photo_url is invalid", msg)
		logged = log.call_args.kwargs["message"]
		self.assertIn("cover=https://x/cover.jpg", logged)
		self.assertNotIn("Nội dung", logged)  # không ghi nội dung bài

	def test_parse_keeps_raw_message(self):
		from omni_crm.channels.zalo.client import ZaloClient
		from omni_crm.channels.zalo.errors import ZaloError

		resp = mock.Mock(status_code=200)
		resp.json.return_value = {"error": -201, "message": "title is invalid"}
		with self.assertRaises(ZaloError) as ctx:
			ZaloClient._parse(resp)
		self.assertEqual(ctx.exception.raw_message, "title is invalid")

	def test_poll_timeout_is_provider_error(self):
		fake = FakeZaloClient([{"token": "tk"}] + [{"status": "processing"}] * 40)
		with mock.patch.object(zalo, "_client", return_value=fake):
			with self.assertRaises(ProviderError) as ctx:
				zalo.ZaloAdapter().publish(self.payload(), sleep=lambda s: None)
		self.assertTrue(ctx.exception.ambiguous)

	def test_title_too_long_validation(self):
		from omni_crm.marketing.publisher.errors import PublishValidationError

		with self.assertRaises(PublishValidationError):
			content.validate(self.payload(title="x" * 151))


class TestImages(FrappeTestCase):
	def test_private_image_rejected(self):
		from omni_crm.marketing.publisher.errors import PublishValidationError

		with self.assertRaisesRegex(PublishValidationError, "riêng tư"):
			content.public_image_url("/private/files/a.jpg")
		with self.assertRaises(PublishValidationError):
			content.public_image_url("http://insecure/a.jpg")
		self.assertEqual(content.public_image_url("https://cdn/a.jpg"), "https://cdn/a.jpg")
