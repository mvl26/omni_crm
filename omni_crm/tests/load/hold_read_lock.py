"""Sự cố "DB chậm khi tra cứu": `LOCK TABLES ... WRITE` chặn cả ĐỌC trên Omni Identity và CRM Lead trong N giây.

	env/bin/python apps/omni_crm/omni_crm/tests/load/hold_read_lock.py sites/omni.test/site_config.json 20

Chạy bằng tài khoản DB của site (đọc site_config.json, không in mật khẩu). Chỉ dùng trên site test.
"""

from __future__ import annotations

import json
import sys
import time

import pymysql


def main() -> None:
	"""Khóa ghi hai bảng `seconds` giây rồi nhả."""
	conf = json.load(open(sys.argv[1], encoding="utf-8"))
	seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
	conn = pymysql.connect(
		host=conf.get("db_host", "127.0.0.1"),
		port=int(conf.get("db_port", 3306)),
		user=conf["db_name"],
		password=conf["db_password"],
		database=conf["db_name"],
	)
	try:
		with conn.cursor() as cur:
			cur.execute("LOCK TABLES `tabOmni Identity` WRITE, `tabCRM Lead` WRITE")
			print(f"đang khóa đọc/ghi {seconds}s", flush=True)
			time.sleep(seconds)
			cur.execute("UNLOCK TABLES")
	finally:
		conn.close()
		print("đã nhả khóa", flush=True)


if __name__ == "__main__":
	main()
