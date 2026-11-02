"""Máy chủ Graph API GIẢ cho tải/sự cố (V2-08). Chỉ stdlib; KHÔNG gọi nền tảng thật.

	python fake_graph.py --port 8777 --control /tmp/graph.mode [--leads /tmp/graph_missed.json]

Tệp điều khiển: `ok` (mặc định) hoặc `down` (mọi yêu cầu trả 500). Tệp `--leads`: JSON {"<form_id>": [<lead Graph>...]} để
trả cho `GET /<version>/<form_id>/leads` (đối soát). Nhận `GET /<version>/<leadgen_id>?fields=...` với id dạng `LOADADS-M-<i>`.
Đếm số yêu cầu vào tệp `<control>.count`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ads_load_common import PREFIX, meta_lead

LOCK = threading.Lock()


def make_handler(control: str, leads_file: str | None):
	count = {"n": 0, "down": 0}

	class H(BaseHTTPRequestHandler):
		def log_message(self, *a):  # im lặng
			pass

		def _send(self, status: int, payload: dict) -> None:
			body = json.dumps(payload).encode()
			self.send_response(status)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)

		def do_GET(self):
			with LOCK:
				count["n"] += 1
			mode = open(control).read().strip() if os.path.exists(control) else "ok"
			if mode == "down":
				with LOCK:
					count["down"] += 1
					open(control + ".count", "w").write(json.dumps(count))
				return self._send(500, {"error": {"code": 2, "message": "giả lập lỗi"}})
			url = urlparse(self.path)
			q = parse_qs(url.query)
			parts = [p for p in url.path.split("/") if p]  # [version, id, (leads)]
			if len(parts) == 3 and parts[2] == "leads":
				data = json.load(open(leads_file)) if leads_file and os.path.exists(leads_file) else {}
				return self._send(200, {"data": data.get(parts[1], [])})
			ident = parts[1] if len(parts) > 1 else ""
			m = re.fullmatch(rf"{PREFIX}-M-(\d+)", ident)
			if m:
				lead = meta_lead(ident, int(m.group(1)))
				if q.get("fields") == ["custom_disclaimer_responses"]:
					return self._send(200, {"custom_disclaimer_responses": []})
				return self._send(200, lead)
			if ident.startswith(f"{PREFIX}AD"):
				return self._send(
					200,
					{
						"id": ident,
						"name": f"Ad {ident}",
						"adset_id": f"{PREFIX}ADSET",
						"campaign_id": f"{PREFIX}CAMP",
						"adset": {"name": "Adset tai"},
						"campaign": {"name": f"{PREFIX} chien dich"},
					},
				)
			return self._send(400, {"error": {"code": 100, "message": "không có"}})

	return H


def main() -> None:
	"""Chạy máy chủ."""
	p = argparse.ArgumentParser()
	p.add_argument("--port", type=int, default=8777)
	p.add_argument("--control", default="/tmp/graph.mode")
	p.add_argument("--leads", default=None)
	a = p.parse_args()
	srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.control, a.leads))
	srv.serve_forever()


if __name__ == "__main__":
	main()
