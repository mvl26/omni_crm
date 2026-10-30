"""Worker RQ chạy trên Redis của SITE (site_config.json: redis_queue), không dùng Redis của bench. Chỉ cho đo tải/sự cố.

	env/bin/python apps/omni_crm/omni_crm/tests/load/rq_worker.py omni.test omni,default,short [--fake-ai <tệp điều khiển>]

`bench worker` chỉ đọc common_site_config.json nên không dùng được với Redis tạm; script này `frappe.init(<site>)`
để lấy redis_queue của site (đặt tạm trong site_config.json khi đo, khôi phục sau đó).
`--fake-ai <tệp>`: cài FakeAIClient; nội dung tệp điều khiển: `ok` (trả kết quả tối thiểu), `fail` (mọi lần gọi lỗi
HTTP 400 giả), hoặc `slow:<giây>`. Không bao giờ gọi AI thật.
"""

from __future__ import annotations

import os
import sys
import time

import frappe
from frappe.utils.background_jobs import Worker, get_queue_list, get_redis_conn


def _install_fake_ai(control: str) -> None:
	from omni_crm.bot import ai_client
	from omni_crm.bot.fake_client import FakeAIClient

	class Controlled(FakeAIClient):
		def create(self, **kw):
			mode = open(control, encoding="utf-8").read().strip() if os.path.exists(control) else "ok"
			if mode == "fail":
				self.faults = ["fatal"]
			elif mode.startswith("slow:"):
				time.sleep(float(mode.split(":")[1]))
			self.calls = []  # không tích lũy bộ nhớ khi chạy lâu
			return super().create(**kw)

	ai_client.set_client(Controlled())


def _install_trace(path: str) -> None:
	"""Ghi kết quả của job việc gọi lại ra tệp (chỉ để chẩn đoán khi đo tải)."""
	from omni_crm.channels.telephony import callbacks

	real = callbacks.process_missed_call

	def traced(call_name, channel=None):
		import json as _json

		row = frappe.db.get_value(
			"Omni Call", call_name, ["status", "direction", "lead", "callback_task"], as_dict=True
		)
		try:
			result = real(call_name, channel)
			err = None
		except BaseException as e:
			result, err = None, type(e).__name__
		with open(path, "a", encoding="utf-8") as f:
			f.write(
				_json.dumps({"call": call_name, "before": row, "result": result, "err": err}, default=str)
				+ "\n"
			)
		return result

	callbacks.process_missed_call = traced


def main() -> None:
	"""Khởi động một worker trên các hàng đợi chỉ định."""
	site, queues = sys.argv[1], sys.argv[2]
	fake = sys.argv[sys.argv.index("--fake-ai") + 1] if "--fake-ai" in sys.argv else None
	os.chdir(os.path.join(os.path.dirname(frappe.__file__), "..", "..", "..", "sites"))
	frappe.init(site, sites_path=".")
	conn = get_redis_conn()
	qs = get_queue_list([q.strip() for q in queues.split(",")], build_queue_name=True)
	if fake:
		_install_fake_ai(fake)  # biến toàn cục của module, các tiến trình con của RQ kế thừa khi fork
	if os.environ.get("OMNI_LOAD_TRACE"):
		frappe.connect()
		_install_trace(os.environ["OMNI_LOAD_TRACE"])
		frappe.destroy()
		frappe.init(site, sites_path=".")
	# Như supervisor khởi động lại worker: mất kết nối Redis (sự cố) thì thử nối lại chứ không thoát
	while True:
		try:
			Worker(qs, connection=get_redis_conn()).work(logging_level="WARNING", date_format="%H:%M:%S")
			break
		except Exception as e:
			print(f"worker dừng: {type(e).__name__}; thử lại sau 5 giây", flush=True)
			time.sleep(5)


if __name__ == "__main__":
	main()
