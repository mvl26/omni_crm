"""Chạy thử nghiệm chuyển giọng nói D3-03: mọi nhà cung cấp đã đăng ký x mọi ghi âm trong một thư mục NGOÀI repo.

    bench --site <site> execute omni_crm.tests.stt_trial.run_trial.run --kwargs \
        "{'audio_dir': '/duong/dan/ghi_am', 'out_dir': '/duong/dan/ket_qua', 'providers': ['TenNhaCungCap'], \
          'pricing_file': '/duong/dan/bang_gia.json'}"

Thư mục ghi âm: `<tên>.wav|mp3|m4a|...`, bản chuẩn gõ tay `<tên>.txt` (tùy chọn), meta `<tên>.json`
({"stereo": true, "synthetic": false, "duration_seconds": 95}). Kết quả (JSON, bảng tổng hợp, bảng đánh dấu CSV)
ghi ra `out_dir`; cả hai thư mục bị từ chối nếu nằm trong repo. Mọi lời gọi đi qua `omni_crm.stt.client.transcribe`
(cổng Q11, thử lại có giới hạn). Văn bản thu được chạy qua bot phân tích bằng `ai_client.call_tool`, cũng chịu
cổng Q11; chỉ ghi âm gắn `synthetic: true` mới được gửi ra ngoài khi Q11 chưa chốt.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from omni_crm.bot import ai_client
from omni_crm.bot import analyze as analyze_mod
from omni_crm.bot.context import render_turns
from omni_crm.bot.prompts.loader import load_prompt, render
from omni_crm.stt import client as stt_client
from omni_crm.stt import registry, trial
from omni_crm.stt.base import STTBlockedError, STTError

DEFAULT_ANALYZE_MODEL = "claude-haiku-5-5"
RESULT_FILE = "ket-qua.json"
SUMMARY_CSV = "bang-tong-hop.csv"
REVIEW_CSV = "bang-danh-dau.csv"
SUMMARY_COLUMNS = (
	"provider",
	"recordings",
	"failed",
	"references",
	"wer",
	"avg_seconds",
	"cost_amount",
	"cost_currency",
	"avg_confidence",
	"has_speaker_labels",
)


def analyze_transcript(text: str, *, synthetic: bool) -> dict:
	"""Chạy bot phân tích trên một văn bản; {"accepted": ...} hoặc {"unmeasured": lý do}. Không ghi Lead hay CRM."""
	settings = frappe.get_cached_doc("Omni Settings")
	prompt = load_prompt("analyze")
	mask = not cint(frappe.conf.get(analyze_mod.UNMASK_CONF))
	turns = [{"actor": "customer", "channel_type": "Telephony", "text": text}]
	batch_id = f"stt-trial::{frappe.generate_hash(length=10)}"
	raw = ai_client.call_tool(
		"Analyze",
		settings.get("analyze_model") or DEFAULT_ANALYZE_MODEL,
		render(prompt),
		[
			{
				"role": "user",
				"content": render_turns(turns, analyze_mod.MAX_TURNS, analyze_mod.MAX_CHARS, mask),
			}
		],
		analyze_mod.TOOL,
		batch_id=batch_id,
		prompt_version=prompt.version,
		max_tokens=1200,
		synthetic=synthetic,  # chỉ True khi ghi âm được gắn nhãn tổng hợp; văn bản khách thật luôn qua cổng Q11
		eval_mode=True,
	)
	if raw is None:
		status = frappe.db.get_value(
			"Omni Bot Run", {"batch_id": batch_id}, ["status", "error"], as_dict=True
		)
		reason = f"{status.status} {status.error or ''}".strip() if status else "không có kết quả"
		return {"unmeasured": f"analyze: {reason}"}
	customer = " ".join(t["text"] for t in turns)
	return {"accepted": analyze_mod.postprocess(raw, customer)["accepted"]}


def _transcribe_one(provider: Any, rec: trial.Recording, audio: bytes) -> dict:
	row: dict[str, Any] = {"recording": rec.name, "provider": provider.name, "ok": False}
	started = time.perf_counter()
	try:
		result = stt_client.transcribe(provider, audio, rec.mime, stereo=rec.stereo, synthetic=rec.synthetic)
	except STTBlockedError:
		row.update(error="Q11: chưa được phép gửi ghi âm khách ra ngoài", blocked=True)
	except STTError as e:
		row.update(error=str(e))
	else:
		row.update(
			ok=True,
			text=result.text,
			segments=result.segments,
			confidence=result.confidence,
			has_speaker_labels=any(s.get("speaker") for s in result.segments),
		)
	row["seconds"] = round(time.perf_counter() - started, 3)
	return row


def _provider_summary(name: str, rows: list[dict], pricing: dict) -> dict:
	ok = [r for r in rows if r["ok"]]
	with_ref = [r for r in ok if r.get("wer") is not None]
	durations = [r["audio_seconds"] for r in rows if r.get("audio_seconds") is not None]
	total_seconds = sum(durations) if len(durations) == len(rows) and rows else None
	confs = [r["confidence"] for r in ok if r.get("confidence") is not None]
	return {
		"recordings": len(rows),
		"failed": len(rows) - len(ok),
		"blocked": sum(1 for r in rows if r.get("blocked")),
		"references": len(with_ref),
		"wer": trial.pool_wer([trial.WordErrors(*r["word_errors"]) for r in with_ref]),
		"avg_seconds": (sum(r["seconds"] for r in ok) / len(ok)) if ok else None,
		"cost": trial.estimate_cost(pricing, name, total_seconds),
		"avg_confidence": (sum(confs) / len(confs)) if confs else None,
		"has_speaker_labels": any(r.get("has_speaker_labels") for r in ok),
	}


def run(
	audio_dir: str,
	out_dir: str,
	providers: list[str] | None = None,
	pricing_file: str | None = None,
	analyze: bool = True,
	thresholds: dict | None = None,
) -> dict:
	"""Chạy thử nghiệm; ghi ket-qua.json, bang-tong-hop.csv, bang-danh-dau.csv vào `out_dir`; trả bản tổng hợp."""
	audio_path, out_path = trial.assert_outside_repo(audio_dir), trial.assert_outside_repo(out_dir)
	out_path.mkdir(parents=True, exist_ok=True)
	names = providers or registry.available_names()
	if not names:
		raise ValueError(
			_(
				"Chưa có nhà cung cấp chuyển giọng nói nào được đăng ký (thêm adapter theo docs/nghiem-thu/stt-thu-nghiem.md)"
			)
		)
	pricing = trial.load_pricing(pricing_file)
	th = trial.Thresholds(**(thresholds or {}))
	recordings = trial.discover_recordings(audio_path)

	results: list[dict] = []
	review: list[dict] = []
	for name in names:
		provider = registry.get_provider(name)
		for rec in recordings:
			row = _transcribe_one(provider, rec, rec.path.read_bytes())
			row["audio_seconds"] = trial.audio_seconds(rec.path, rec.meta)
			row["wer"] = None  # chỉ có khi ghi âm có bản chuẩn và chuyển giọng nói thành công
			if row["ok"] and rec.reference:
				errs = trial.word_errors(rec.reference, row["text"])
				row["wer"] = trial.wer(rec.reference, row["text"])
				row["word_errors"] = [errs.substitutions, errs.deletions, errs.insertions, errs.ref_words]
			if row["ok"] and analyze and row["text"].strip():
				row["analysis"] = analyze_transcript(row["text"], synthetic=rec.synthetic)
				review.extend(trial.review_rows(rec.name, name, row["analysis"].get("accepted") or {}))
			results.append(row)

	summaries = {n: _provider_summary(n, [r for r in results if r["provider"] == n], pricing) for n in names}
	decision = trial.overall(
		{
			n: trial.decide(
				s["wer"], None, s["references"], th
			)  # tỉ lệ trường đúng: chờ người duyệt đánh dấu
			for n, s in summaries.items()
		}
	)
	summary: dict[str, Any] = {
		"recordings": len(recordings),
		"thresholds": asdict(th),
		"providers": summaries,
		"decision": decision,
		"results_file": str(out_path / RESULT_FILE),
	}
	(out_path / RESULT_FILE).write_text(
		json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8"
	)
	_write_summary_csv(out_path / SUMMARY_CSV, summaries)
	if review:
		trial.write_review_csv(out_path / REVIEW_CSV, review)
		summary["review_csv"] = str(out_path / REVIEW_CSV)
	return summary


def _write_summary_csv(path: Path, summaries: dict[str, dict]) -> None:
	with open(path, "w", newline="", encoding="utf-8-sig") as f:
		w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
		w.writeheader()
		for name, s in summaries.items():
			cost = s["cost"] or {}
			w.writerow(
				{
					"provider": trial.safe_cell(name),
					"recordings": s["recordings"],
					"failed": s["failed"],
					"references": s["references"],
					"wer": "" if s["wer"] is None else f"{s['wer']:.4f}",
					"avg_seconds": "" if s["avg_seconds"] is None else f"{s['avg_seconds']:.2f}",
					"cost_amount": "" if not cost else f"{cost['amount']:.4f}",
					"cost_currency": cost.get("currency", ""),
					"avg_confidence": "" if s["avg_confidence"] is None else f"{s['avg_confidence']:.3f}",
					"has_speaker_labels": int(s["has_speaker_labels"]),
				}
			)


def summarize_review(out_dir: str, review_csv: str | None = None, thresholds: dict | None = None) -> dict:
	"""Sau khi người duyệt đánh dấu `bang-danh-dau.csv`: gộp tỉ lệ trường đúng với WER đã lưu và đưa ra nhánh theo ngưỡng."""
	out_path = trial.assert_outside_repo(out_dir)
	saved = json.loads((out_path / RESULT_FILE).read_text(encoding="utf-8"))["summary"]["providers"]
	review = trial.summarize_review(review_csv or str(out_path / REVIEW_CSV))
	th = trial.Thresholds(**(thresholds or {}))
	per = {
		n: trial.decide(s["wer"], (review.get(n) or {}).get("accuracy"), s["references"], th)
		for n, s in saved.items()
	}
	return {"review": review, "decision": trial.overall(per)}


def cli() -> None:
	"""Dòng lệnh: python -m omni_crm.tests.stt_trial.run_trial --site omni.test --audio-dir ... --out-dir ... [--provider A --provider B]."""
	import argparse

	p = argparse.ArgumentParser(description="Thử nghiệm chuyển giọng nói D3-03")
	p.add_argument("--site", required=True)
	p.add_argument("--audio-dir", help="bắt buộc trừ khi --summarize-review")
	p.add_argument("--out-dir", required=True)
	p.add_argument("--provider", action="append")
	p.add_argument("--pricing-file")
	p.add_argument("--no-analyze", action="store_true")
	p.add_argument(
		"--summarize-review", action="store_true", help="gộp bảng đánh dấu đã điền, không chạy lại"
	)
	a = p.parse_args()
	frappe.init(site=a.site)
	frappe.connect()
	try:
		if a.summarize_review:
			out = summarize_review(a.out_dir)
		else:
			if not a.audio_dir:
				p.error("--audio-dir là bắt buộc")
			out = run(a.audio_dir, a.out_dir, a.provider, a.pricing_file, analyze=not a.no_analyze)
		print(json.dumps(out, ensure_ascii=False, indent=1))
	finally:
		frappe.destroy()


if __name__ == "__main__":
	cli()
