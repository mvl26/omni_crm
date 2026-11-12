"""Watcher spool intake: dấu vết thư mục đổi khi có file mới, đẩy đúng một lượt nạp mỗi lần đổi, chịu lỗi không chết."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omni_crm.intake_files import watcher


class TestWatcher(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)
		self.inbox = self.root / "inbox"
		for day in ("2026/10/31", "2026/11/01", "2026/11/11"):
			(self.inbox / day / "gmail").mkdir(parents=True)
		(self.inbox / "2026" / "11" / "ghi-chu").mkdir()  # thư mục lạ: bỏ qua

	def write(self, day: str, name: str) -> None:
		d = self.inbox / day / "gmail"
		tmp = d / f".{name}.tmp"
		tmp.write_text("{}", encoding="utf-8")
		os.replace(tmp, d / name)  # intake ghi kiểu này
		st = d.stat()
		os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # chắc chắn mtime đổi dù đồng hồ thô

	def test_hai_ngay_moi_nhat_qua_ranh_gioi_thang(self):
		days = watcher.latest_days(self.inbox)
		self.assertEqual([d.relative_to(self.inbox).as_posix() for d in days], ["2026/11/11", "2026/11/01"])
		(self.inbox / "2026" / "11").rename(self.inbox / "2026" / "x11")  # chỉ còn tháng 10
		self.assertEqual(
			[d.relative_to(self.inbox).as_posix() for d in watcher.latest_days(self.inbox)], ["2026/10/31"]
		)

	def test_dau_vet_doi_khi_co_file_moi_va_khong_doi_khi_yen(self):
		a = watcher.signature(self.root, {"gmail"})
		self.assertEqual(a, watcher.signature(self.root, {"gmail"}))
		self.write("2026/11/11", "m1.json")
		self.assertNotEqual(a, watcher.signature(self.root, {"gmail"}))

	def test_ngay_moi_lam_doi_dau_vet(self):
		a = watcher.signature(self.root, {"gmail"})
		(self.inbox / "2026" / "11" / "12" / "gmail").mkdir(parents=True)
		self.assertNotEqual(a, watcher.signature(self.root, {"gmail"}))

	def run_loops(self, n, sleep_effect=None, root=None, kick_effect=None):
		with (
			mock.patch.object(watcher.runner, "spool_root", return_value=root or self.root),
			mock.patch.object(watcher.runner, "enabled_dirs", return_value={"gmail"}),
			mock.patch.object(watcher.runner, "kick", side_effect=kick_effect) as kick,
			mock.patch.object(watcher.time, "sleep", side_effect=sleep_effect) as sl,
			mock.patch.object(watcher, "_log"),
		):
			watcher.run(interval=1.0, max_loops=n)
		return kick, sl

	def test_day_mot_luot_luc_dau_va_moi_lan_co_file(self):
		sleeps = []

		def on_sleep(sec):
			sleeps.append(sec)
			if len(sleeps) == 2:
				self.write("2026/11/11", "m1.json")

		kick, _ = self.run_loops(4, on_sleep)
		# vòng 1: lần đầu -> đẩy; vòng 2: yên; vòng 3: có file -> đẩy; vòng 4: yên
		self.assertEqual(kick.call_count, 2)
		self.assertEqual(sleeps, [1.0] * 4)

	def test_chua_cau_hinh_spool_thi_cho_khong_day(self):
		with (
			mock.patch.object(watcher.runner, "spool_root", return_value=None),
			mock.patch.object(watcher.runner, "kick") as kick,
			mock.patch.object(watcher.time, "sleep") as sl,
			mock.patch.object(watcher, "_log"),
		):
			watcher.run(max_loops=2)
		kick.assert_not_called()
		sl.assert_called_with(watcher.RETRY_SECONDS)

	def test_loi_redis_khong_lam_chet_tien_trinh_va_day_lai_khi_hoi_phuc(self):
		kick, sl = self.run_loops(2, kick_effect=[ConnectionError("redis chết"), None])
		self.assertEqual(kick.call_count, 2)  # vòng 2 đẩy lại dù thư mục không đổi
		self.assertEqual(sl.call_args_list[0].args, (watcher.RETRY_SECONDS,))
