import threading

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.pipeline.locks import LockBusy, identity_lock


def _in_thread(fn):
	"""Chạy fn trong thread có kết nối Frappe riêng; trả (thread, kết quả dict)."""
	site = frappe.local.site
	result: dict = {}

	def run():
		frappe.init(site=site)
		frappe.connect()
		try:
			result["value"] = fn()
		except BaseException as e:
			result["error"] = e
		finally:
			frappe.destroy()

	t = threading.Thread(target=run)
	t.start()
	return t, result


class TestLocks(FrappeTestCase):
	def test_second_acquire_raises_lock_busy(self):
		with identity_lock("Zalo OA:PLTlock1", wait=1):
			with self.assertRaises(LockBusy):
				with identity_lock("Zalo OA:PLTlock1", wait=1):
					pass

	def test_released_after_exit_and_on_exception(self):
		with identity_lock("Zalo OA:PLTlock2", wait=1):
			pass
		with self.assertRaises(RuntimeError):
			with identity_lock("Zalo OA:PLTlock2", wait=1):
				raise RuntimeError("x")
		with identity_lock("Zalo OA:PLTlock2", wait=1):
			pass  # lấy lại được

	def test_different_keys_do_not_block(self):
		with identity_lock("Zalo OA:PLTlock3", wait=1):
			with identity_lock("Zalo OA:PLTlock4", wait=1):
				pass

	def test_lock_across_threads(self):
		entered, release = threading.Event(), threading.Event()

		def holder():
			with identity_lock("Zalo OA:PLTlock5", wait=1):
				entered.set()
				release.wait(10)

		t1, _ = _in_thread(holder)
		self.assertTrue(entered.wait(10))

		def contender():
			with identity_lock("Zalo OA:PLTlock5", wait=1):
				return "got"

		t2, res = _in_thread(contender)
		t2.join(10)
		self.assertIsInstance(res.get("error"), LockBusy)
		release.set()
		t1.join(10)

	def test_expired_lock_release_does_not_raise(self):
		with identity_lock("Zalo OA:PLTlock6", timeout=1, wait=1):
			import time

			time.sleep(1.5)  # khóa hết hạn trong lúc xử lý
