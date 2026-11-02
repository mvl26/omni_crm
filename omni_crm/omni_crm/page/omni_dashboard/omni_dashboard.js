// Tổng quan CSKH (docs/13 mục 1). Chỉ hiển thị khối mà API trả về: khối tắt không có mặt, không hiện số 0 giả.
frappe.pages["omni-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Tổng quan CSKH"), single_column: true });
	wrapper.omni_dashboard = new OmniDashboard(page);
};
frappe.pages["omni-dashboard"].on_page_show = function (wrapper) {
	wrapper.omni_dashboard && wrapper.omni_dashboard.start();
};
frappe.pages["omni-dashboard"].on_page_hide = function (wrapper) {
	wrapper.omni_dashboard && wrapper.omni_dashboard.stop();
};

class OmniDashboard {
	constructor(page) {
		this.page = page;
		this.period = "today";
		this.scope = null;
		this.$root = $('<div class="omni-ui"></div>').appendTo(page.body);
		this.timer = null;
	}
	start() {
		this.load();
		this.stop();
		this.timer = setInterval(() => this.load(), 60000);
	}
	stop() {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
	}
	load() {
		frappe.call({
			method: "omni_crm.api.dashboard.snapshot",
			args: { period: this.period, scope: this.scope },
			callback: (r) => this.render(r.message || {}),
		});
	}
	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	render(d) {
		const e = this.esc.bind(this);
		if (!d.enabled) {
			this.$root.html(`<div class="ou-card">${e(d.message || __("Dashboard CSKH đang tắt"))}</div>`);
			return;
		}
		const b = d.blocks || {};
		let h = `<div class="ou-head"><div><h2>${e(__("Tổng quan CSKH"))}</h2>
			<span class="muted">${e(__("Cập nhật"))} ${e(d.generated_at || "")}</span></div><div>
			<select class="ou-period">${[["today", __("Hôm nay")], ["7d", __("7 ngày")], ["30d", __("30 ngày")]]
				.map(([k, l]) => `<option value="${k}" ${k === d.period ? "selected" : ""}>${e(l)}</option>`).join("")}</select>
			${d.can_team ? `<select class="ou-scope"><option value="team" ${d.scope === "team" ? "selected" : ""}>${e(__("Toàn đội"))}</option>
				<option value="mine" ${d.scope === "mine" ? "selected" : ""}>${e(__("Của tôi"))}</option></select>` : ""}
			<button class="ou-btn primary ou-inbox">${e(__("Mở hộp thư"))}</button></div></div>`;
		h += '<div class="ou-tiles">';
		for (const k of ["tile_waiting", "tile_first_response", "tile_new_leads", "tile_missed_calls", "tile_hot_leads", "tile_drafts"]) {
			if (b[k]) h += this.tile(b[k]);
		}
		h += "</div>";
		if (b.attention) h += this.attention(b.attention);
		h += '<div class="ou-grid2">';
		if (b.channels) h += this.channels(b.channels);
		if (b.bot_today) h += this.bot(b.bot_today);
		h += "</div>";
		if (b.staff) h += this.staff(b.staff);
		h += '<div class="ou-grid2">';
		if (b.funnel) h += this.funnel(b.funnel);
		if (b.tier_region) h += this.tierRegion(b.tier_region);
		h += "</div>";
		if (b.prospects_ab) h += this.prospects(b.prospects_ab);
		if (b.campaigns) h += this.campaigns(b.campaigns);
		this.$root.html(h);
		this.$root.find(".ou-period").on("change", (ev) => { this.period = ev.target.value; this.load(); });
		this.$root.find(".ou-scope").on("change", (ev) => { this.scope = ev.target.value; this.load(); });
		this.$root.find(".ou-inbox").on("click", () => frappe.set_route("omni-inbox"));
		this.$root.find("[data-lead]").on("click", (ev) => frappe.set_route("omni-customer", $(ev.currentTarget).data("lead")));
		this.$root.find("[data-inbox]").on("click", (ev) => {
			frappe.route_options = { lead: $(ev.currentTarget).data("inbox") };
			frappe.set_route("omni-inbox");
		});
	}
	tile(t) {
		const e = this.esc.bind(this);
		const val = t.value == null ? "—" : t.value;
		const dl = t.delta ? `<div class="${t.delta.good ? "good" : "bad"}">${t.delta.value > 0 ? "+" : ""}${e(t.delta.value)} ${e(t.delta.label)}</div>` : "";
		const extra = t.overdue ? `<div class="bad">${e(__("Quá hạn"))}: ${e(t.overdue)}</div>` : "";
		return `<div class="ou-card ou-tile"><div class="l">${e(t.label)}</div><div class="v">${e(val)}</div>
			<div class="muted">${e(t.unit || "")}</div>${dl}${extra}</div>`;
	}
	attention(a) {
		const e = this.esc.bind(this);
		const rows = a.rows.map((r) => `<tr><td><b>${e(r.customer)}</b><div class="muted">${e(r.organization)}</div></td>
			<td>${e(r.channel || "")}</td><td>${e(r.last_text)}</td><td class="mono">${e(r.waited_minutes)} ${e(__("phút"))}</td>
			<td><span class="tag ${r.reason === "complaint" || r.reason === "overdue" ? "red" : "navy"}">${e(r.reason_label)}</span></td>
			<td>${e(r.owner_name)}</td><td><button class="ou-btn" data-inbox="${e(r.lead)}">${e(__("Mở"))}</button></td></tr>`).join("");
		return `<div class="ou-card"><h3>${e(__("Cần xử lý ngay"))} <span class="muted">(${e(a.rows.length)}/${e(a.total)})</span></h3>
			<table><tr><th>${e(__("Khách hàng"))}</th><th>${e(__("Kênh"))}</th><th>${e(__("Tin gần nhất"))}</th><th>${e(__("Đã chờ"))}</th>
			<th>${e(__("Lý do ưu tiên"))}</th><th>${e(__("Phụ trách"))}</th><th></th></tr>${rows || `<tr><td colspan="7" class="muted">${e(__("Không có gì cần xử lý"))}</td></tr>`}</table></div>`;
	}
	channels(c) {
		const e = this.esc.bind(this);
		return `<div class="ou-card"><h3>${e(__("Theo kênh"))}</h3>${c.rows.map((r) => `<div><b>${e(r.label)}</b>
			<span class="muted">${e(r.contacts)} ${e(__("liên hệ"))} · ${e(r.new_leads)} ${e(__("Lead mới"))}</span>
			<div class="bar"><span style="width:${Math.round(r.ratio * 100)}%"></span></div></div>`).join("")}</div>`;
	}
	bot(t) {
		const e = this.esc.bind(this);
		return `<div class="ou-card"><h3>${e(__("Bot hôm nay"))}</h3><div>${e(__("Nháp đã soạn"))}: <b>${e(t.drafted)}</b> ·
			${e(__("Gửi nguyên văn"))}: <b>${e(t.sent_as_is)}</b> · ${e(__("Sửa/bỏ"))}: <b>${e(t.edited_or_discarded)}</b> ·
			${e(__("Chuyển người"))}: <b>${e(t.handoff)}</b></div>${t.reasons.map((r) => `<div class="muted">${e(r.reason)}: ${e(r.count)}</div>`).join("")}</div>`;
	}
	staff(s) {
		const e = this.esc.bind(this);
		const cb = s.columns.callbacks, dr = s.columns.drafts;
		const rows = s.rows.map((r) => `<tr><td>${e(r.name)}</td><td>${e(r.open)}</td><td>${e(r.waiting)}</td>
			<td>${r.first_response == null ? "—" : e(r.first_response)}</td>${cb ? `<td>${e(r.overdue_callbacks)}</td>` : ""}${dr ? `<td>${e(r.drafts_approved)}</td>` : ""}</tr>`).join("");
		return `<div class="ou-card"><h3>${e(__("Theo nhân viên"))}</h3><table><tr><th>${e(__("Nhân viên"))}</th><th>${e(__("Đang phụ trách"))}</th>
			<th>${e(__("Chờ trả lời"))}</th><th>${e(__("Phản hồi lần đầu TB"))}</th>${cb ? `<th>${e(__("Gọi lại quá hạn"))}</th>` : ""}${dr ? `<th>${e(__("Nháp đã duyệt"))}</th>` : ""}</tr>${rows}</table></div>`;
	}
	funnel(f) {
		const e = this.esc.bind(this);
		return `<div class="ou-card"><h3>${e(__("Phễu giai đoạn"))}</h3>${f.stages.map((s) => `<div>${e(s.stage)}
			<span class="muted">(${e(s.kind)})</span> <b>${e(s.count)}</b><div class="bar"><span style="width:${Math.round(s.ratio * 100)}%"></span></div></div>`).join("")}</div>`;
	}
	tierRegion(t) {
		const e = this.esc.bind(this);
		const keys = ["Miền Bắc", "Miền Trung", "Miền Nam", "unknown"];
		return `<div class="ou-card"><h3>${e(__("Nhà thầu theo hạng và khu vực"))}</h3><table><tr><th>${e(__("Hạng"))}</th>${t.regions.map((r) => `<th>${e(r)}</th>`).join("")}<th>${e(__("Tổng"))}</th></tr>
			${t.rows.map((r) => `<tr><td><b>${e(r.tier)}</b></td>${keys.map((k) => `<td>${e(r.regions[k] || 0)}</td>`).join("")}<td>${e(r.total)}</td></tr>`).join("")}</table></div>`;
	}
	campaigns(c) {
		const e = this.esc.bind(this);
		const v = (x) => (x == null ? "—" : e(x));
		const rows = c.rows.map((r) => `<tr><td>${e(r.label)}<div class="muted">${e(r.platform || "")}</div></td><td>${e(r.leads)}</td><td>${e(r.valid)}</td>
			<td>${r.on_time_pct == null ? "—" : e(r.on_time_pct) + "%"}</td><td>${v(r.median_first_response)}</td><td>${e(r.tier_ab)}</td><td>${e(r.progressed)}</td>
			<td>${e(r.deals)}</td><td>${v(r.spend)}</td><td>${v(r.cost_per_valid_lead)}</td></tr>`).join("");
		return `<div class="ou-card"><h3>${e(__("Theo chiến dịch"))}</h3><table><tr><th>${e(__("Chiến dịch"))}</th><th>${e(__("Lead"))}</th><th>${e(__("Hợp lệ"))}</th>
			<th>${e(__("Phản hồi trong hạn"))}</th><th>${e(__("Phản hồi lần đầu (trung vị, phút)"))}</th><th>${e(__("Nhà thầu hạng A/B"))}</th><th>${e(__("Đã trao đổi nhu cầu+"))}</th>
			<th>${e(__("Deal"))}</th><th>${e(__("Chi phí"))}</th><th>${e(__("Chi phí/Lead hợp lệ"))}</th></tr>${rows || `<tr><td colspan="10" class="muted">${e(__("Không có"))}</td></tr>`}</table>
			<div class="muted">${e(__("Không tính Lead thử nghiệm. Chi phí mỗi Lead hợp lệ tính trên cả chiến dịch, không theo kỳ."))}</div></div>`;
	}
	prospects(p) {
		const e = this.esc.bind(this);
		const rows = p.rows.map((r) => `<tr><td><a data-lead="${e(r.lead)}" href="#" onclick="return false">${e(r.organization)}</a>
			<div class="muted mono">${e(r.omni_tax_id || "")}</div></td><td><span class="tag navy">${e(r.omni_fit_tier)}</span></td><td>${e(r.omni_fit_score)}</td>
			<td>${e(r.omni_region || "")}</td><td>${e(r.owner_name)}</td><td><button class="ou-btn" data-lead="${e(r.lead)}">${e(__("Ghi nhật ký"))}</button></td></tr>`).join("");
		return `<div class="ou-card"><h3>${e(__("Phù hợp A/B, chưa liên hệ"))}</h3><table><tr><th>${e(__("Nhà thầu"))}</th><th>${e(__("Hạng"))}</th><th>${e(__("Điểm"))}</th>
			<th>${e(__("Khu vực"))}</th><th>${e(__("Phụ trách"))}</th><th></th></tr>${rows || `<tr><td colspan="6" class="muted">${e(__("Không có"))}</td></tr>`}</table></div>`;
	}
}
