"""Kịch bản sự cố "cơ sở dữ liệu chậm" (docs/10 mục 5): giữ khóa dòng/khoảng trên bảng tin vào trong N giây.

Chạy NGOÀI bench, bằng tài khoản DB riêng của site (đọc từ site_config.json; không cần quyền root):

	env/bin/python apps/omni_crm/omni_crm/tests/load/hold_db_lock.py sites/omni.test/site_config.json 25

Trong lúc đó gọi webhook (send_burst.py hoặc curl) và đo thời gian phản hồi. `SELECT ... FOR UPDATE` trên cả
bảng ở mức REPEATABLE READ khóa cả khoảng chèn, nên INSERT tin mới phải chờ tới khi script nhả (hoặc tới
`innodb_lock_wait_timeout`, mặc định 50 giây). Chỉ chạy trên site test. Mật khẩu không được in ra.
"""

from __future__ import annotations

import json
import sys
import time

import pymysql


def main() -> None:
	"""Giữ khóa `seconds` giây rồi rollback."""
	conf = json.load(open(sys.argv[1], encoding="utf-8"))
	seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
	conn = pymysql.connect(
		host=conf.get("db_host", "127.0.0.1"),
		port=int(conf.get("db_port", 3306)),
		user=conf["db_name"],
		password=conf["db_password"],
		database=conf["db_name"],
		autocommit=False,
	)
	try:
		with conn.cursor() as cur:
			cur.execute("select count(*) from `tabOmni Inbound Message` for update")
			print(f"đang giữ khóa {seconds}s", flush=True)
			time.sleep(seconds)
	finally:
		conn.rollback()
		conn.close()
		print("đã nhả khóa", flush=True)


if __name__ == "__main__":
	main()
