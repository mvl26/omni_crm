// Chi tiết khách / Chi tiết nhà thầu (docs/13 mục 3b, docs/14 mục 8). Màn 3 (Hồ sơ chăm sóc) là tab "Nhật ký chăm sóc".
// Mọi quyền do API kiểm theo Lead; giao diện chỉ ẩn nút theo cờ `actions` mà server trả về.
frappe.pages["omni-customer"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Chi tiết khách"), single_column: true });
	wrapper.omni_customer = new OmniCustomer(page);
};
frappe.pages["omni-customer"].on_page_show = function (wrapper) {
	const lead = frappe.get_route()[1];
	if (lead && wrapper.omni_customer) wrapper.omni_customer.open(lead);
};

class OmniCustomer {
	constructor(page) {
		this.page = page;
		this.lead = null;
		this.tab = "activity";
		this.filter = "all";
		this.$root = $('<div class="omni-ui"></div>').appendTo(page.body);
	}
	e(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	call(method, args) {
		return frappe.call({ method: `omni_crm.api.customer.${method}`, args });
	}
	open(lead) {
		this.lead = lead;
		this.call("get_header", { lead }).then((r) => {
			this.header = r.message;
			this.page.set_title(this.header.title);
			this.renderShell();
		});
	}
	renderShell() {
		const e = this.e.bind(this), h = this.header, a = h.actions;
		const crumbs = `<a href="/app/omni-inbox">${e(__("Hộp thư"))}</a> / ${e(__("Lead"))} / <span class="mono">${e(h.lead)}</span>`;
		const tabs = [["activity", __("Hoạt động")], ["care", __("Nhật ký chăm sóc")], ["awards", __("Gói thầu đã trúng")]];
		if (h.features.telephony) tabs.push(["calls", __("Cuộc gọi")]);
		this.$root.html(`
			<div class="muted">${crumbs}</div>
			<div class="ou-head"><div><h2>${e(h.title)} ${h.tax_id ? `<span class="mono muted">MST ${e(h.tax_id)}</span>` : ""}
				${h.organization && h.organization.omni_fit_tier ? `<span class="tag navy">${e(__("Hạng"))} ${e(h.organization.omni_fit_tier)}</span>` : ""}
				${h.hot ? `<span class="tag red">${e(__("Lead nóng"))} ${e(h.score)}</span>` : ""}
				${a.person_refused ? `<span class="tag red">${e(__("Từ chối nhận liên hệ"))}</span>` : ""}</h2>
				<div class="muted">${e(h.subtitle)} ${h.channels.map((c) => `<span class="tag">${e(c)}</span>`).join(" ")}</div></div>
				<div>
				${a.can_call ? `<a class="ou-btn" href="tel:${e(a.phone)}">${e(__("Gọi"))} <span class="mono">${e(a.phone)}</span></a>` : ""}
				${a.can_message ? `<button class="ou-btn oc-zalo">${e(__("Nhắn Zalo"))}</button>` : ""}
				${a.can_email ? `<a class="ou-btn" href="/crm/leads/${e(h.lead)}">${e(__("Gửi email"))}</a>` : ""}
				${h.care_log_enabled && a.can_write ? `<button class="ou-btn oc-log">${e(__("Ghi nhật ký chăm sóc"))}</button>` : ""}
				<a class="ou-btn" href="/crm/leads/${e(h.lead)}">${e(__("Mở Lead trong CRM"))}</a>
				${a.can_convert ? `<button class="ou-btn primary oc-convert">${e(__("Chuyển thành Deal"))}</button>` : ""}
				</div></div>
			<div class="stages">${this.stageBar()}</div>
			<div class="ou-grid3">
				<div class="oc-left"></div>
				<div><div class="ou-tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === this.tab ? "on" : ""}">${e(l)}</button>`).join("")}</div>
					<div class="oc-tab"></div></div>
				<div class="oc-right"></div>
			</div>`);
		this.$root.find("[data-tab]").on("click", (ev) => {
			this.tab = $(ev.currentTarget).data("tab");
			this.$root.find("[data-tab]").removeClass("on");
			$(ev.currentTarget).addClass("on");
			this.renderTab();
		});
		this.$root.find(".oc-zalo").on("click", () => { frappe.route_options = { lead: this.lead }; frappe.set_route("omni-inbox"); });
		this.$root.find(".oc-log").on("click", () => { this.tab = "care"; this.renderShell(); setTimeout(() => this.logForm(), 300); });
		this.$root.find(".oc-convert").on("click", () => this.convert());
		this.renderTab();
		this.renderLeft();
		this.renderRight();
	}
	stageBar() {
		const s = this.header.stages, e = this.e.bind(this);
		const lead = s.lead_stages.map((x) => `<span class="${!s.deal && x.name === s.lead_status ? "on" : ""}">${e(x.name)}</span>`).join("");
		const deal = s.deal ? s.deal_stages.map((x) => `<span class="${x.name === s.deal_status ? "on" : ""}">${e(x.name)}</span>`).join("") : "";
		return lead + deal;
	}
	convert() {
		// Hành động chuyển đổi có sẵn của CRM (không tự viết lại logic).
		frappe.confirm(__("Chuyển Lead này thành Deal?"), () => {
			frappe.call({
				method: "crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal",
				args: { lead: this.lead },
				callback: () => this.open(this.lead),
			});
		});
	}
	renderTab() {
		const $t = this.$root.find(".oc-tab");
		({ activity: () => this.activity($t), care: () => this.care($t), awards: () => this.awards($t), calls: () => this.activity($t, "call") })[this.tab]();
	}
	activity($t, kind) {
		const e = this.e.bind(this);
		const f = kind || this.filter;
		this.call("get_activity", { lead: this.lead, kind: f }).then((r) => {
			const filters = kind ? "" : `<div class="ou-tabs">${[["all", __("Tất cả")], ["message", __("Tin nhắn")], ["call", __("Cuộc gọi")], ["care", __("Nhật ký")], ["system", __("Hệ thống")]]
				.map(([k, l]) => `<button data-f="${k}" class="${k === f ? "on" : ""}">${e(l)}</button>`).join("")}</div>`;
			$t.html(filters + r.message.rows.map((x) => `<div class="ev ${x.direction === "in" ? "in" : ""}"><b>${e(x.title)}</b>
				<span class="tag">${e(x.source)}</span> <span class="muted mono">${e(x.at)}</span><div>${e(x.detail)}</div></div>`).join("") ||
				`<div class="muted">${e(__("Chưa có hoạt động"))}</div>`);
			$t.find("[data-f]").on("click", (ev) => { this.filter = $(ev.currentTarget).data("f"); this.activity($t); });
		});
	}
	care($t) {
		const e = this.e.bind(this);
		this.call("get_care_logs", { lead: this.lead }).then((r) => {
			const d = r.message;
			this.careData = d;
			$t.html(`${d.can_write && this.header.care_log_enabled ? `<button class="ou-btn primary oc-new">${e(__("Ghi nhật ký chăm sóc"))}</button>` : ""}
				<div class="oc-form"></div>
				<table><tr><th>${e(__("Phiên"))}</th><th>${e(__("Kênh"))}</th><th>${e(__("Người xử lý"))}</th><th>${e(__("Tóm tắt"))}</th><th>${e(__("Kết quả"))}</th><th>${e(__("Bước tiếp theo"))}</th></tr>
				${d.rows.map((x) => `<tr><td class="mono">${e(x.started_at)}</td><td>${e(x.channel)}</td><td>${e(x.handled_name || x.handled_kind)}</td><td>${e(x.summary)}</td>
				<td>${x.outcome ? e(x.outcome) : x.unrecorded ? `<span class="tag red">${e(__("Chưa ghi kết quả"))}</span>` : ""}</td>
				<td>${e(x.next_action)} <span class="muted mono">${e(x.next_due)}</span></td></tr>`).join("")}</table>
				<div class="muted">${e(d.total)} ${e(__("phiên"))}</div>`);
			$t.find(".oc-new").on("click", () => this.logForm());
		});
	}
	logForm() {
		const e = this.e.bind(this), d = this.careData;
		if (!d) return;
		const open = d.open_session;
		this.$root.find(".oc-form").html(`<div class="ou-card ou-form"><div class="ou-grid2"><div>
			<label>${e(__("Kênh"))}<select class="f-channel">${d.channels.map((c) => `<option ${open && open.channel === c ? "selected" : ""}>${e(c)}</option>`).join("")}</select></label>
			<label>${e(__("Thời gian"))}<input class="f-at" type="datetime-local"></label>
			<label>${e(__("Tóm tắt"))}<textarea class="f-summary">${e(open ? open.summary : "")}</textarea></label>
			<label>${e(__("Kết quả"))}<select class="f-outcome"><option></option>${d.outcomes.map((c) => `<option>${e(c)}</option>`).join("")}</select></label>
			<label>${e(__("Bước tiếp theo"))}<input class="f-next"></label>
			<label>${e(__("Hạn"))}<input class="f-due" type="datetime-local"></label>
			<label><input class="f-task" type="checkbox" checked style="width:auto"> ${e(__("Tạo việc trong CRM"))}</label>
			<button class="ou-btn primary f-save">${e(__("Lưu"))}</button></div>
			<div class="muted"><b>${e(__("Quy ước kết quả"))}</b><br>${e(__("Mọi kết quả cần bước tiếp theo và hạn, trừ: ") + d.no_next_outcomes.join(", "))}.<br>
			${e(__("Hẹn gọi lại: có hạn cụ thể. Không liên lạc được: chưa tính là đã liên hệ. Đã chốt đơn: hệ thống chỉ gợi ý chuyển Deal."))}</div></div></div>`);
		const iso = (v) => (v ? v.replace("T", " ") + ":00" : null);
		this.$root.find(".f-save").on("click", () => {
			this.call("save_care_log", {
				lead: this.lead, log: open ? open.name : null, channel: this.$root.find(".f-channel").val(),
				at: iso(this.$root.find(".f-at").val()), summary: this.$root.find(".f-summary").val(),
				outcome: this.$root.find(".f-outcome").val(), next_action: this.$root.find(".f-next").val(),
				next_due: iso(this.$root.find(".f-due").val()), create_task: this.$root.find(".f-task").is(":checked") ? 1 : 0,
			}).then((r) => {
				if (r.message && r.message.suggest_deal) frappe.show_alert(__("Gợi ý: chuyển Lead thành Deal"));
				this.open(this.lead);
			});
		});
	}
	awards($t) {
		const e = this.e.bind(this);
		this.call("get_awards", { lead: this.lead }).then((r) => {
			const d = r.message;
			$t.html(`<table><tr><th>${e(__("Bệnh viện"))}</th><th>${e(__("Số TBMT"))}</th><th>${e(__("Loại hàng"))}</th><th>${e(__("Ngày"))}</th>
				<th>${e(__("Giá trị"))}</th><th>${e(__("Mã hàng"))}</th><th>${e(__("Còn hiệu lực"))}</th></tr>
				${d.rows.map((x) => `<tr><td>${e(x.hospital)}</td><td class="mono">${e(x.notice_no)}</td><td>${e(x.category)}</td><td class="mono">${e(x.award_date)}</td>
				<td class="mono">${e(format_currency(x.award_value, "VND", 0))}</td><td>${e(x.item_count)}</td><td>${x.active ? `<span class="tag green">${e(__("Còn"))}</span>` : `<span class="tag">${e(__("Hết"))}</span>`}</td></tr>`).join("")}</table>
				<div class="muted">${e(d.total)} ${e(__("gói"))}</div>`);
		});
	}
	renderLeft() {
		const e = this.e.bind(this), $l = this.$root.find(".oc-left");
		Promise.all([this.call("get_profile", { lead: this.lead }), this.call("get_contacts", { lead: this.lead })]).then(([p, c]) => {
			let h = p.message.groups.map((g) => `<div class="ou-card"><h3>${e(g.label)}</h3>${g.items.map((i) => `<div class="muted">${e(i.label)}
				${i.source ? `<span class="tag">${e(i.source)}</span>` : ""}</div><div class="oc-field" data-field="${e(i.field)}" data-edit="${i.editable ? 1 : 0}">${e(i.value) || "—"}</div>`).join("")}</div>`).join("");
			h += `<div class="ou-card"><h3>${e(__("Kênh đã liên kết"))}</h3>${p.message.channels.map((x) => `<div>${e(x.channel_type)} <span class="muted">${e(x.name)}</span>
				${x.is_follower === true ? `<span class="tag green">${e(__("Quan tâm OA"))}</span>` : ""}<div class="muted mono">${e(x.last_inbound_at)}</div></div>`).join("")}</div>`;
			if (c.message.organization) {
				h += `<div class="ou-card"><h3>${e(__("Người liên hệ"))}</h3>${c.message.rows.map((x) => `<div><b>${e(x.full_name)}</b> <span class="muted">${e(x.designation)}</span>
					${x.role ? `<span class="tag">${e(x.role)}</span>` : ""} <span class="tag ${x.refused ? "red" : ""}">${e(x.refused ? __("Từ chối") : x.consent)}</span>
					${x.can_call ? `<a class="ou-btn" href="tel:${e(x.phone)}">${e(__("Gọi"))}</a>` : ""}</div>`).join("") || `<div class="muted">—</div>`}</div>`;
			}
			$l.html(h);
			$l.find('[data-edit="1"]').on("dblclick", (ev) => {
				const f = $(ev.currentTarget).data("field");
				frappe.prompt({ fieldname: "v", fieldtype: "Data", label: f, default: $(ev.currentTarget).text() === "—" ? "" : $(ev.currentTarget).text() },
					(v) => this.call("update_field", { lead: this.lead, field: f, value: v.v }).then(() => this.renderLeft()), __("Sửa"));
			});
		});
	}
	renderRight() {
		const e = this.e.bind(this), $r = this.$root.find(".oc-right");
		Promise.all([this.call("get_fit", { lead: this.lead }), this.call("get_side", { lead: this.lead })]).then(([f, s]) => {
			const fit = f.message.fit, en = f.message.engagement;
			let h = "";
			if (fit) {
				h += `<div class="ou-card"><h3>${e(__("Mức phù hợp"))} <span class="tag navy">${e(__("Hạng"))} ${e(fit.tier)}</span> <b>${e(fit.score)}</b></h3>
					${fit.criteria.map((c) => `<div>${c.met ? "✔" : "✘"} ${e(c.label)}: <b>${e(c.value)}</b> / ${e(c.required)}</div>`).join("")}
					${fit.stale ? `<div class="bad">${e(__("Điểm lưu đã cũ so với tham số hiện hành; sẽ tính lại ban đêm"))}</div>` : ""}
					<details><summary>${e(__("Cách tính"))}</summary><div class="muted">${e(fit.formula)}</div>
					${fit.components.map((c) => `<div class="muted">${e(c.label)}: ${e(c.value)} / ${e(c.max)} × ${e(c.weight)} = ${e(c.points.toFixed(1))}</div>`).join("")}</details></div>`;
			}
			if (en) h += `<div class="ou-card"><h3>${e(__("Điểm quan tâm"))} <b>${e(en.score)}</b> <span class="muted">/ ${e(en.threshold)}</span></h3>${en.reasons.map((x) => `<div class="muted">${e(x)}</div>`).join("")}</div>`;
			if (f.message.package) h += `<div class="ou-card"><h3>${e(__("Gói đề xuất"))}</h3>${e(f.message.package)}</div>`;
			h += `<div class="ou-card"><h3>${e(__("Việc cần làm"))}</h3>${s.message.tasks.map((t) => `<div><input type="checkbox" class="oc-task" data-task="${e(t.name)}" ${t.done ? "checked" : ""}
				${s.message.can_write ? "" : "disabled"}> ${e(t.title)} <span class="muted mono">${e(t.due_date || "")}</span></div>`).join("") || `<div class="muted">—</div>`}</div>`;
			if (s.message.duplicates.length) {
				h += `<div class="ou-card"><h3>${e(__("Nghi trùng"))}</h3>${s.message.duplicates.map((d) => `<div>${e(d.other_name)} <span class="muted">${e(d.reason)}</span>
					<button class="ou-btn oc-merge" data-c="${e(d.name)}">${e(__("Gộp vào Lead này"))}</button> <button class="ou-btn oc-dismiss" data-c="${e(d.name)}">${e(__("Không phải trùng"))}</button></div>`).join("")}</div>`;
			}
			h += `<div class="ou-card"><h3>${e(__("Quản lý"))}</h3><div class="muted">${e(__("Số ngày liên hệ trong 30 ngày"))}: <b>${e(s.message.contacts_30d)}</b></div></div>`;
			$r.html(h);
			$r.find(".oc-task").on("change", (ev) => this.call("set_task_done", { task: $(ev.currentTarget).data("task"), done: ev.currentTarget.checked ? 1 : 0 }));
			$r.find(".oc-merge").on("click", (ev) => frappe.call({ method: "omni_crm.identity.duplicates.merge", args: { candidate: $(ev.currentTarget).data("c"), primary: this.lead }, callback: () => this.open(this.lead) }));
			$r.find(".oc-dismiss").on("click", (ev) => frappe.call({ method: "omni_crm.identity.duplicates.dismiss", args: { candidate: $(ev.currentTarget).data("c") }, callback: () => this.renderRight() }));
		});
	}
}
