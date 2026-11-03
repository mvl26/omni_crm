// Màn hình giám sát Omni (D1-10): chỉ Omni Admin. Số liệu từ omni_crm.api.admin.monitor_snapshot, tự làm mới 30 giây.
frappe.pages["omni-monitor"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Giám sát Omni"),
		single_column: true,
	});
	wrapper.omni_monitor = new OmniMonitor(page);
	// Frappe chỉ phát sự kiện jQuery "hide" cho trang: dừng bộ đếm khi rời trang.
	$(wrapper).on("hide", () => wrapper.omni_monitor && wrapper.omni_monitor.pause());
};

frappe.pages["omni-monitor"].on_page_show = function (wrapper) {
	if (wrapper.omni_monitor) wrapper.omni_monitor.resume();
};

const REFRESH_MS = 30000;

class OmniMonitor {
	constructor(page) {
		this.page = page;
		this.timer = null;
		this.last_manual = [];
		$(page.body).html(`<div class="omni-monitor">
			<div class="om-meta"></div>
			<div class="om-grid"></div>
			<div class="om-card om-manual-card" style="margin-top:12px"></div>
			<div class="om-card om-erase" style="margin-top:12px"></div>
		</div>`);
		this.$meta = $(page.body).find(".om-meta");
		this.$grid = $(page.body).find(".om-grid");
		this.$manual = $(page.body).find(".om-manual-card");
		this.$erase = $(page.body).find(".om-erase");
		page.set_primary_action(__("Quét ngay"), () => this.sweep(), "refresh");
		page.add_inner_button(__("Làm mới"), () => this.load());
		this.render_erase();
		this.resume();
	}

	resume() {
		if (this.timer) return;
		this.load();
		this.timer = setInterval(() => this.load(true), REFRESH_MS);
	}

	pause() {
		clearInterval(this.timer);
		this.timer = null;
	}

	load(silent) {
		return frappe
			.call({ method: "omni_crm.api.admin.monitor_snapshot", freeze: false, silent: !!silent })
			.then((r) => this.render(r.message || {}));
	}

	load_manual() {
		return frappe
			.call({ method: "omni_crm.api.admin.list_manual", freeze: false, silent: true })
			.then((r) => this.render_manual(r.message || []));
	}

	// --- hiển thị ---
	row(k, v, bad) {
		const e = frappe.utils.escape_html;
		return `<div class="om-row"><span class="om-k">${e(k)}</span><span class="${bad ? "om-bad" : ""}">${e(String(v))}</span></div>`;
	}

	dur(sec) {
		if (sec === null || sec === undefined) return "-";
		if (sec < 90) return `${sec} ${__("giây")}`;
		return `${Math.round(sec / 60)} ${__("phút")}`;
	}

	card(title, body) {
		return `<div class="om-card"><h6>${frappe.utils.escape_html(title)}</h6>${body}</div>`;
	}

	yes_no(v) {
		return v ? __("Bật") : __("Tắt");
	}

	render(s) {
		const sw = s.switches || {}, q = s.queue || {}, t = s.timing || {}, o = s.outbound || {};
		const e = frappe.utils.escape_html;
		this.$meta.text(`${__("Cập nhật")}: ${s.generated_at || ""}  (${__("tự làm mới mỗi 30 giây")})`);
		const cards = [];

		cards.push(this.card(__("Công tắc"),
			this.row("sandbox_mode", this.yes_no(sw.sandbox_mode)) +
			this.row("developer_mode", this.yes_no(sw.developer_mode), sw.developer_mode) +
			this.row(__("Email cảnh báo (alert_recipients)"), sw.alert_recipients_set ? __("Đã đặt") : __("Chưa đặt"), !sw.alert_recipients_set) +
			this.row(__("Giữ raw_payload (ngày)"), sw.raw_payload_retention_days || __("Tắt dọn")) +
			(sw.zalo_signature_debug_until ? this.row(__("Chẩn đoán chữ ký Zalo ĐANG BẬT tới"), sw.zalo_signature_debug_until, true) : "") +
			(s.channels || []).map((c) =>
				this.row(c.name, `${__("nhận")}: ${this.yes_no(c.enabled)} / ${__("gửi")}: ${this.yes_no(c.outbound_enabled)}`
					+ (c.skip_signature ? ` / ${__("bỏ qua chữ ký")}` : ""), c.skip_signature)).join("")));

		const ch_html = (s.channels || []).map((c) => {
			let h = `<div class="om-sub"><b>${e(c.name)}</b> <span class="text-muted">${e(c.channel_type)}</span>`;
			h += this.row(__("Nhận tin gần nhất"), c.last_inbound_at || "-");
			h += this.row(__("Gửi thành công gần nhất"), c.last_outbound_at || "-");
			if (c.channel_type === "Zalo OA") {
				const left = c.token_seconds_left;
				h += this.row(__("Hạn token Zalo"), c.token_expires_at ? `${c.token_expires_at} (${left > 0 ? __("còn") + " " + this.dur(left) : __("đã hết hạn")})` : "-",
					left !== null && left < 3600);
			}
			if (c.channel_type === "Zalo OA") {
				h += this.row(__("Gói OA"), c.oa_package_warning
					? `${c.oa_package || __("Chưa xác nhận")} - ${__("Gói OA có thể chưa cho kết nối API")}` : (c.oa_package || "-"), !!c.oa_package_warning);
			}
			if (c.channel_type === "Meta Lead Ads" && c.meta_token) {
				const m = c.meta_token;
				let txt = "-";
				if (m.checked_at) {
					txt = m.never_expires ? __("không hết hạn") : (m.expires_at
						? `${m.expires_at} (${m.seconds_left > 0 ? __("còn") + " " + this.dur(m.seconds_left) : __("đã hết hạn")})` : "-");
					if (m.data_expires_at) txt += ` / ${__("quyền dữ liệu")}: ${m.data_expires_at}`;
				}
				h += this.row(__("Hạn token Meta"), m.checked_at ? `${txt} - ${m.note || ""}` : __("chưa kiểm"), m.problem);
			}
			if (c.last_error) h += this.row(__("Lỗi gần nhất"), c.last_error, true);
			if (c.channel_type === "Zalo OA") {
				h += `<button class="btn btn-xs btn-default om-refresh-token" data-channel="${e(c.name)}">${__("Làm mới token Zalo")}</button>`;
			}
			return h + "</div>";
		}).join("");
		cards.push(this.card(__("Kênh"), ch_html || `<span class="text-muted">${__("Chưa có kênh")}</span>`));

		cards.push(this.card(__("Hàng đợi"),
			this.row("New", q.new) + this.row("Processing", q.processing) + this.row("Retry", q.retry) +
			this.row("Manual", q.manual, q.manual > 0) +
			this.row(__("Tuổi tin New lâu nhất"), this.dur(q.oldest_new_age_seconds), q.oldest_new_age_seconds > 300) +
			this.row(__("Lần quét gần nhất"), s.sweeper && s.sweeper.last_run_age_seconds !== null ? this.dur(s.sweeper.last_run_age_seconds) + " " + __("trước") : __("chưa có"), s.sweeper && s.sweeper.stale)));

		const sp = s.spool || {};
		if (sp.count || sp.failed_at) {
			cards.push(this.card(__("Hàng dự phòng webhook"),
				this.row(__("Số tin đang chờ chèn lại"), sp.count, sp.count > 0) +
				this.row(__("Tin cũ nhất"), sp.oldest_age_seconds === null || sp.oldest_age_seconds === undefined ? "-" : this.dur(sp.oldest_age_seconds), sp.stale) +
				this.row(__("Dung lượng (byte)"), sp.bytes) +
				this.row(__("Lần cuối DB và hàng dự phòng cùng hỏng"), sp.failed_at || "-", !!sp.failed_at)));
		}

		const mr = s.meta_requeue || {};
		if (mr.last_run || mr.manual_tam_thoi || mr.manual_vinh_vien || mr.requeued_total) {
			const lr = mr.last_run;
			cards.push(this.card(__("Kéo lại lead Meta"),
				this.row(__("Lượt đối soát gần nhất"), lr ? `${lr.at} - ${__("tạo mới")} ${lr.created}, ${__("kéo lại")} ${lr.requeued}` : __("chưa có")) +
				this.row(__("Tin kéo lại ở lượt gần nhất"), lr ? lr.requeued : "-") +
				this.row(__("Tổng số lần đã kéo lại"), mr.requeued_total) +
				this.row(__("Manual - lỗi tạm thời (đợi kéo lại)"), mr.manual_tam_thoi, mr.manual_tam_thoi > 0) +
				this.row(__("Manual - lỗi vĩnh viễn (cần người)"), mr.manual_vinh_vien, mr.manual_vinh_vien > 0) +
				this.row(__("Dừng kéo lại (quá 3 lần)"), lr ? lr.exhausted : "-", lr && lr.exhausted > 0)));
		}

		cards.push(this.card(__("Thời gian xử lý (24 giờ)"),
			this.row(__("Số tin Done"), t.count) + this.row("p50", this.dur(t.p50)) + this.row("p95", this.dur(t.p95), t.p95 > 120)));

		cards.push(this.card(__("Chiều ra (hôm nay)"),
			this.row("Queued", o.queued) + this.row("Sent", o.sent) + this.row("Failed", o.failed) + this.row("Blocked", o.blocked) +
			this.row(__("Failed trong 1 giờ"), o.failed_last_hour, o.failed_last_hour > 5)));

		const tel = s.telephony || {};
		if ((tel.channels || []).length) {
			cards.push(this.card(__("Tổng đài (hôm nay)"),
				this.row(__("Cuộc gọi"), tel.calls_today) + this.row(__("Đã nghe máy"), tel.answered_today) +
				this.row(__("Gọi nhỡ"), tel.missed_today, tel.missed_today > 0) +
				this.row(__("Việc gọi lại quá hạn"), tel.callbacks_overdue, tel.callbacks_overdue > 0) +
				tel.channels.map((c) => {
					const r = (tel.reconcile || {})[c.name];
					const u = (tel.unverified_calls || {})[c.name];
					const rec = !r ? __("chưa chạy") : r.skipped ? __("bỏ qua (sandbox)") : r.error ? `${__("LỖI")}: ${r.error}` :
						`${r.date}: ${__("tổng")} ${r.total}, ${__("đã có")} ${r.existing}, ${__("bù thêm")} ${r.filled}`;
					const bad = !!r && (!!r.error || (r.total && r.filled * 100 / r.total > 1));
					return this.row(`${c.name} · ${__("đối soát đêm")}`, rec, bad) +
						this.row(`${c.name} · ${__("cuộc gọi nhỡ chưa xác minh")}`, u === null || u === undefined ? "-" : u, u > 0);
				}).join("") +
				tel.channels.map((c) => this.row(c.name, `${this.yes_no(c.enabled)} · ${__("cuộc gọi gần nhất")}: ${c.last_call_at || "-"}`)).join("")));
		}

		const bot = s.bot || {};
		if (bot.daily_token_budget !== undefined) {
			const pct = bot.usage_pct === null || bot.usage_pct === undefined ? "-" : bot.usage_pct + "%";
			cards.push(this.card(__("Bot AI"),
				this.row(__("Token hôm nay / hạn mức"), `${bot.tokens_today} / ${bot.daily_token_budget} (${pct})`, bot.usage_pct > 80) +
				this.row(__("Số lần gọi AI (24 giờ)"), bot.runs_24h) +
				this.row(__("Lần lỗi (24 giờ)"), bot.errors_24h, bot.runs_24h >= 20 && bot.errors_24h * 100 > 5 * bot.runs_24h) +
				this.row(__("Bị bỏ qua do hạn mức (24 giờ)"), bot.skipped_budget_24h, bot.skipped_budget_24h > 0)));
		}

		const d = s.duplicates || {};
		cards.push(this.card(__("Nghi trùng"), this.row(__("Cặp đang Open"), d.open, d.open > 20)));

		this.$grid.html(cards.join(""));
		this.$grid.find(".om-refresh-token").on("click", (ev) => this.refresh_token($(ev.currentTarget).data("channel")));
		this.load_manual();
	}

	render_manual(rows) {
		const e = frappe.utils.escape_html;
		this.last_manual = rows;
		const body = rows.map((r) => `<tr>
			<td><input type="checkbox" class="om-pick" value="${e(r.name)}"></td>
			<td>${e(r.channel || "")}</td><td>${e(r.event_type || "")}</td><td>${e(String(r.attempts))}</td>
			<td>${e(r.received_at || "")}</td><td>${e(r.last_error || "")}</td></tr>`).join("");
		this.$manual.html(`<h6>${__("Tin Manual")} (${rows.length})</h6>` + (rows.length ? `
			<table class="table table-bordered om-manual"><thead><tr><th></th><th>${__("Kênh")}</th><th>${__("Loại")}</th>
			<th>${__("Lần thử")}</th><th>${__("Nhận lúc")}</th><th>${__("Lỗi")}</th></tr></thead><tbody>${body}</tbody></table>
			<button class="btn btn-sm btn-primary om-reprocess">${__("Xử lý lại")}</button>` : `<span class="text-muted">${__("Không có tin Manual.")}</span>`));
		this.$manual.find(".om-reprocess").on("click", () => this.reprocess());
	}

	render_erase() {
		this.$erase.html(`<h6>${__("Xóa dữ liệu khách")}</h6>
			<div class="text-muted" style="font-size:12px;margin-bottom:6px">${__("Xóa nội dung tin vào/ra (văn bản, tệp, payload thô, hồ sơ người gửi) của một Lead và các định danh của nó. Giữ bản ghi kỹ thuật; không xóa Lead. Không hoàn tác được.")}</div>
			<input type="text" class="form-control om-erase-lead" placeholder="CRM-LEAD-...">
			<textarea class="form-control om-erase-reason" rows="2" placeholder="${__("Lý do (bắt buộc)")}"></textarea>
			<button class="btn btn-sm btn-danger om-erase-go">${__("Xóa dữ liệu khách")}</button>`);
		this.$erase.find(".om-erase-go").on("click", () => this.erase());
	}

	// --- thao tác ---
	sweep() {
		frappe.call({ method: "omni_crm.api.admin.sweep_now", freeze: true }).then((r) => {
			const c = r.message || {};
			frappe.show_alert({ message: __("Đã quét: kẹt {0}, thử lại {1}, tin New mồ côi {2}, tin ra {3}", [c.stuck, c.retry_due, c.new_orphan, c.outbound]), indicator: "green" });
			this.load();
		});
	}

	refresh_token(channel) {
		frappe.call({ method: "omni_crm.api.admin.refresh_zalo_token", args: { channel }, freeze: true }).then((r) => {
			const done = (r.message || {}).refreshed;
			frappe.show_alert({ message: done ? __("Đã làm mới token") : __("Chưa làm mới (tiến trình khác đang làm hoặc token còn mới)"), indicator: done ? "green" : "orange" });
			this.load();
		});
	}

	reprocess() {
		const names = this.$manual.find(".om-pick:checked").map((i, el) => el.value).get();
		if (!names.length) {
			frappe.msgprint(__("Chọn ít nhất một tin."));
			return;
		}
		frappe.call({ method: "omni_crm.api.admin.reprocess", args: { inbound_names: names }, freeze: true }).then((r) => {
			frappe.show_alert({ message: __("Đã đưa {0} tin về New", [(r.message || {}).reset]), indicator: "green" });
			this.load();
		});
	}

	erase() {
		const lead = (this.$erase.find(".om-erase-lead").val() || "").trim();
		const reason = (this.$erase.find(".om-erase-reason").val() || "").trim();
		if (!lead || !reason) {
			frappe.msgprint(__("Nhập Lead và lý do."));
			return;
		}
		frappe.confirm(
			__("Xóa vĩnh viễn nội dung tin của {0}? Không hoàn tác được.", [frappe.utils.escape_html(lead)]),
			() => frappe.call({ method: "omni_crm.api.admin.erase_customer_data", args: { lead, reason }, freeze: true }).then((r) => {
				const m = r.message || {};
				frappe.msgprint(__("Đã xóa nội dung {0} tin vào và {1} tin ra.", [m.inbound, m.outbound]));
				this.$erase.find(".om-erase-lead, .om-erase-reason").val("");
			})
		);
	}
}
