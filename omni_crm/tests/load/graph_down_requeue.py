"""Kịch bản "Graph chết" cho V21-03 (F3), site test + Graph GIẢ + Redis tạm. CHẠY NGOÀI BENCH (cwd = thư mục bench).

	PYTHONPATH=<worktree> FRAPPE_REDIS_QUEUE=redis://127.0.0.1:<p> FRAPPE_REDIS_CACHE=redis://127.0.0.1:<q> \
	env/bin/python apps/omni_crm/omni_crm/tests/load/graph_down_requeue.py --down-minutes 13 --leads 20 --missed 5 --out /tmp/gd.json

Các bước (thời gian được NÉN: Graph chết `--down-minutes` phút, không phải 30; backoff mặc định "1,5" nên tin vào Manual sau ~7 phút
Graph chết, đúng trạng thái V2-08 mục 4.1 chỉ khác thời lượng; tác vụ quét mô phỏng mỗi 60 giây thay cho cron 5 phút):
1. dựng dữ liệu LOADADS, Graph giả `down`; 2. gửi `--leads` webhook Meta (chữ ký thật theo App Secret tổng hợp); 3. chạy `requeue_stuck` mỗi 60 giây
cho tới hết thời gian chết; 4. Graph sống lại; 5. MỘT lượt đối soát (`reconcile`, trỏ client vào Graph giả) và chờ worker; 6. đo; KHÔNG gọi `reprocess`.
Dọn: `ads_setup.cleanup` rồi `verify_burst.cleanup` (Zalo) và tắt mọi tiến trình con. Không gọi nền tảng thật, không gửi gì cho khách.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ads_load_common as C

PY = "env/bin/python"
SITE = "omni.test"
GRAPH_PORT, WEB_PORT = 8777, 8099


def bench_exec(method: str, *args: str) -> str:
	cmd = ["bench", "--site", SITE, "execute", method, *(["--args", json.dumps(list(args))] if args else [])]
	return subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout.strip()


def sql(query: str) -> str:
	return subprocess.run(
		["bench", "--site", SITE, "mariadb", "-N", "-e", query], capture_output=True, text=True
	).stdout.strip()


def counts(stage: str) -> dict:
	rows = sql(
		f"select status, count(*) from `tabOmni Inbound Message` where channel = '{C.CHANNELS['meta']}' group by status"
	)
	out = {"stage": stage, "at": time.strftime("%H:%M:%S")}
	for line in rows.splitlines():
		k, v = line.split("\t")
		out[k] = int(v)
	return out


def main() -> None:
	p = argparse.ArgumentParser()
	p.add_argument("--down-minutes", type=float, default=8)
	p.add_argument("--leads", type=int, default=20)
	p.add_argument("--missed", type=int, default=5, help="lead chỉ có trên Graph (webhook không tới)")
	p.add_argument("--out", default="/tmp/graph_down.json")
	a = p.parse_args()
	ctl, leads_file = "/tmp/v2103_graph.mode", "/tmp/v2103_graph_leads.json"
	procs: list[subprocess.Popen] = []
	report: dict = {
		"note": "site test omni.test, Graph GIẢ, Redis tạm, chưa staging",
		"down_minutes": a.down_minutes,
	}
	try:
		print(bench_exec("omni_crm.tests.load.ads_setup.setup"))
		bench_exec("omni_crm.tests.load.ads_setup.ensure_sources")
		open(ctl, "w").write("down")
		delivered = {f"{C.PREFIX}-M-{i}": i for i in range(1, a.leads + 1)}
		missed = {f"{C.PREFIX}-M-{i}": i for i in range(900, 900 + a.missed)}
		by_form: dict[str, list] = {f: [] for f in C.META_FORMS}
		for lid, i in {**delivered, **missed}.items():
			lead = C.meta_lead(lid, i)
			by_form[lead["form_id"]].append(lead)
		json.dump(by_form, open(leads_file, "w"))
		env = {**os.environ, "OMNI_LOAD_META_SECRET": C.APP_SECRET, "OMNI_LOAD_GOOGLE_KEY": C.GOOGLE_KEY}
		procs.append(
			subprocess.Popen(
				[
					PY,
					f"{HERE}/fake_graph.py",
					"--port",
					str(GRAPH_PORT),
					"--control",
					ctl,
					"--leads",
					leads_file,
				]
			)
		)
		procs.append(
			subprocess.Popen(
				["bench", "--site", SITE, "serve", "--port", str(WEB_PORT)],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
			)
		)
		open("/tmp/v2103_ai.mode", "w").write("ok")
		for _w in range(2):
			procs.append(
				subprocess.Popen(
					[
						PY,
						f"{HERE}/rq_worker.py",
						SITE,
						"omni,default,short",
						"--fake-ai",
						"/tmp/v2103_ai.mode",
						"--fake-graph",
						f"http://127.0.0.1:{GRAPH_PORT}",
					],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
				)
			)
		time.sleep(15)
		t0 = time.time()
		subprocess.run(
			[
				PY,
				f"{HERE}/send_ads_burst.py",
				"--site-url",
				f"http://127.0.0.1:{WEB_PORT}",
				"--host",
				SITE,
				"--leads",
				str(a.leads),
				"--duration",
				"20",
				"--resend-ratio",
				"0",
				"--only",
				"meta",
				"--out",
				"/tmp/v2103_burst.json",
			],
			capture_output=True,
			text=True,
			env=env,
		)
		report["burst_status_codes"] = json.loads(open("/tmp/v2103_burst.json").read())["status_codes"]
		timeline = [counts("sau webhook")]
		while time.time() - t0 < a.down_minutes * 60:
			time.sleep(60)
			bench_exec("omni_crm.pipeline.sweeper.requeue_stuck")
			timeline.append(counts(f"Graph chết, phút {(time.time() - t0) / 60:.1f}"))
			print(timeline[-1], flush=True)
		report["manual_before_recovery"] = counts("trước khi Graph sống lại").get("Manual", 0)
		report["graph_requests_down"] = (
			open(ctl + ".count").read() if os.path.exists(ctl + ".count") else None
		)
		open(ctl, "w").write("ok")
		report["graph_up_at"] = time.strftime("%H:%M:%S")
		report["leads_before_reconcile"] = int(
			sql("select count(*) from `tabCRM Lead` where facebook_lead_id like 'LOADADS%'") or 0
		)
		report["reconcile_result"] = bench_exec("omni_crm.tests.load.ads_setup.reconcile_with_fake_graph")
		deadline = time.time() + 240
		while time.time() < deadline:
			time.sleep(10)
			bench_exec("omni_crm.pipeline.sweeper.requeue_stuck")
			c = counts("chờ worker")
			print(c, flush=True)
			if c.get("Done", 0) >= a.leads + a.missed and not any(
				c.get(k) for k in ("New", "Processing", "Retry", "Manual")
			):
				break
		timeline.append(counts("cuối"))
		report["timeline"] = timeline
		report["final"] = {
			"inbound_rows": int(
				sql(f"select count(*) from `tabOmni Inbound Message` where channel='{C.CHANNELS['meta']}'")
				or 0
			),
			"dedupe_key_distinct": int(
				sql(
					f"select count(distinct dedupe_key) from `tabOmni Inbound Message` where channel='{C.CHANNELS['meta']}'"
				)
				or 0
			),
			"crm_leads": int(
				sql("select count(*) from `tabCRM Lead` where facebook_lead_id like 'LOADADS%'") or 0
			),
			"crm_leads_distinct_fb_id": int(
				sql(
					"select count(distinct facebook_lead_id) from `tabCRM Lead` where facebook_lead_id like 'LOADADS%'"
				)
				or 0
			),
			"leads_without_owner": int(
				sql(
					"select count(*) from `tabCRM Lead` where facebook_lead_id like 'LOADADS%' and (lead_owner is null or lead_owner='')"
				)
				or 0
			),
			"requeued_rows": int(
				sql(
					f"select count(*) from `tabOmni Inbound Message` where channel='{C.CHANNELS['meta']}' and requeue_count>0"
				)
				or 0
			),
			"max_requeue_count": int(
				sql(
					f"select coalesce(max(requeue_count),0) from `tabOmni Inbound Message` where channel='{C.CHANNELS['meta']}'"
				)
				or 0
			),
			"reprocess_calls": 0,
		}
		print(json.dumps(report, ensure_ascii=False, indent=1))
	finally:
		for pr in procs:
			try:
				pr.terminate()
			except Exception:
				pass
		time.sleep(3)
		for pr in procs:
			if pr.poll() is None:
				pr.kill()
		print("cleanup:", bench_exec("omni_crm.tests.load.ads_setup.cleanup"))
		for f in (ctl, ctl + ".count", leads_file, "/tmp/v2103_ai.mode", "/tmp/v2103_burst.json"):
			if os.path.exists(f):
				os.remove(f)
		open(a.out, "w").write(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
	main()
