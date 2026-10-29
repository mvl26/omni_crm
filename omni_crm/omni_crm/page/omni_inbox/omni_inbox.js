// Hộp thư Omni: sale đọc hội thoại đa kênh của Lead và trả lời khách Zalo (D1-09).
frappe.pages["omni-inbox"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Hộp thư Omni"),
		single_column: true,
	});
	wrapper.omni_inbox = new OmniInbox(page, wrapper);
	// Frappe chỉ phát sự kiện jQuery "hide" cho trang (không gọi on_page_hide): bind ở đây.
	$(wrapper).on("hide", () => wrapper.omni_inbox && wrapper.omni_inbox.pause());
};

frappe.pages["omni-inbox"].on_page_show = function (wrapper) {
	const lead = (frappe.route_options && frappe.route_options.lead) || frappe.utils.get_query_params().lead;
	if (frappe.route_options) frappe.route_options = null;
	if (wrapper.omni_inbox) {
		wrapper.omni_inbox.resume();
		wrapper.omni_inbox.show(lead);
	}
};


class OmniInbox {
	constructor(page, wrapper) {
		this.page = page;
		this.current = null;
		this.subscribed = null;
		this.refresh_timer = null;
		const e = frappe.utils.escape_html;
		$(page.body).html(`
			<div class="omni-inbox">
				<div class="oi-left">
					<div class="oi-filters">
						<input type="text" class="form-control oi-search" placeholder="${e(__("Tìm theo tên khách"))}" style="flex:1;min-width:120px">
						<select class="form-control oi-channel" style="width:auto">
							<option value="">${e(__("Mọi kênh"))}</option>
							<option value="Zalo OA">Zalo OA</option>
							<option value="Email">Email</option>
							<option value="Telephony">${e(__("Điện thoại"))}</option>
						</select>
						<label><input type="checkbox" class="oi-unanswered"> ${e(__("Chưa trả lời"))}</label>
						<label><input type="checkbox" class="oi-mine"> ${e(__("Của tôi"))}</label>
					</div>
					<div class="oi-list"></div>
				</div>
				<div class="oi-right">
					<div class="oi-empty">${e(__("Chọn một hội thoại ở bên trái."))}</div>
				</div>
			</div>`);
		this.$list = $(page.body).find(".oi-list");
		this.$right = $(page.body).find(".oi-right");
		$(page.body).find(".oi-search").on("input", frappe.utils.debounce(() => this.load_list(), 300));
		$(page.body).find(".oi-channel, .oi-unanswered, .oi-mine").on("change", () => this.load_list());
		this.on_update_handler = (data) => this.on_update(data);
		this.active = false;
		this.resume();
		this.load_list();
	}

	resume() {
		if (this.active) return;
		this.active = true;
		// Realtime: payload không chứa nội dung tin; nhận tin hiệu rồi gọi lại API có kiểm quyền.
		frappe.realtime.on("omni_inbox_update", this.on_update_handler);
		// Dự phòng khi mất kết nối realtime.
		this.poll = setInterval(() => this.load_list(true), 60000);
		if (this.current) this.show(this.current);
	}

	pause() {
		this.active = false;
		frappe.realtime.off("omni_inbox_update", this.on_update_handler);
		clearInterval(this.poll);
		clearTimeout(this.refresh_timer);
		this.poll = null;
		if (this.subscribed) {
			frappe.realtime.doc_unsubscribe("CRM Lead", this.subscribed);
			this.subscribed = null;
		}
	}

	filters() {
		const b = $(this.page.body);
		return {
			search: b.find(".oi-search").val(),
			channel: b.find(".oi-channel").val(),
			unanswered: b.find(".oi-unanswered").is(":checked") ? 1 : 0,
			mine: b.find(".oi-mine").is(":checked") ? 1 : 0,
		};
	}

	on_update(data) {
		clearTimeout(this.refresh_timer);
		this.refresh_timer = setTimeout(() => {
			this.load_list(true);
			if (data && data.lead && data.lead === this.current) this.load_thread(this.current, true);
		}, 500);
	}

	load_list(silent) {
		frappe
			.call({
				method: "omni_crm.api.inbox.list_conversations",
				args: { filters: this.filters(), page: 1, page_length: 50 },
				freeze: false,
			})
			.then((r) => this.render_list((r.message || {}).rows || []));
	}

	render_list(rows) {
		const e = frappe.utils.escape_html;
		if (!rows.length) {
			this.$list.html(`<div class="oi-empty">${e(__("Chưa có hội thoại nào."))}</div>`);
			return;
		}
		this.$list.html(
			rows
				.map(
					(r) => `
			<div class="oi-item ${r.lead === this.current ? "active" : ""}" data-lead="${e(r.lead)}">
				<div class="oi-top">
					<span>${r.unanswered ? '<span class="oi-dot" title="' + e(__("Chưa trả lời")) + '"></span>' : ""}${e(r.lead_name)}</span>
					<span class="oi-time">${e(frappe.datetime.prettyDate(r.last_at))}</span>
				</div>
				<div class="oi-snippet">${r.last_direction === "out" ? e(__("Bạn")) + ": " : ""}${e(r.last_text || "")}</div>
				<div>${(r.channels || []).map((c) => `<span class="oi-chan">${e(c)}</span>`).join("")}
					<span class="oi-meta">${e(r.lead_owner || "")}</span></div>
			</div>`
				)
				.join("")
		);
		this.$list.find(".oi-item").on("click", (ev) => this.show($(ev.currentTarget).data("lead")));
	}

	show(lead) {
		if (!lead) return;
		this.current = lead;
		this.$list.find(".oi-item").removeClass("active").filter(`[data-lead="${CSS.escape(lead)}"]`).addClass("active");
		// Phòng của Lead: Frappe chỉ nhận đăng ký khi người dùng có quyền đọc Lead.
		if (this.subscribed !== lead) {
			if (this.subscribed) frappe.realtime.doc_unsubscribe("CRM Lead", this.subscribed);
			frappe.realtime.doc_subscribe("CRM Lead", lead);
			this.subscribed = lead;
		}
		this.load_thread(lead);
	}

	load_thread(lead, keep_scroll) {
		frappe
			.call({ method: "omni_crm.api.inbox.get_thread", args: { lead }, freeze: false })
			.then((r) => this.render_thread(r.message, keep_scroll))
			.catch(() => {
				this.$right.html(`<div class="oi-empty">${frappe.utils.escape_html(__("Không có quyền xem hoặc không tìm thấy hội thoại."))}</div>`);
			});
	}

	render_thread(data, keep_scroll) {
		const e = frappe.utils.escape_html;
		const old = this.$right.find(".oi-thread");
		const draft = keep_scroll ? this.$right.find(".oi-text").val() : ""; // giữ bản nháp khi làm mới cùng hội thoại
		const stick = !old.length || !keep_scroll || old[0].scrollHeight - old.scrollTop() - old.innerHeight() < 40;
		const lead = data.lead;
		const msgs = data.messages
			.map((m) => {
				const atts = (m.attachments || [])
					.map((a) => `<div><a href="${e(a.url)}" target="_blank" rel="noopener">${e(a.name)}</a></div>`)
					.join("");
				const labels = {
					Queued: __("Đang gửi"),
					Sent: __("Đã gửi"),
					Failed: __("Lỗi"),
					Blocked: __("Bị chặn"),
				};
				const flag = m.status ? ` · <span class="oi-status ${e(m.status)}">${e(labels[m.status] || m.status)}</span>` : "";
				const why = m.error ? `<div class="oi-error">${e(m.error)}</div>` : "";
				const sandbox = m.is_sandbox ? ` · ${e(__("thử nghiệm"))}` : "";
				return `<div class="oi-msg ${m.direction}">
					<div class="oi-meta"><b>${e(m.channel_type || "")}</b> · ${e(m.sender || "")} · ${e(frappe.datetime.str_to_user(m.at))}${flag}${sandbox}</div>${why}
					<div>${e(m.text).replace(/\n/g, "<br>")}</div>${atts}</div>`;
			})
			.join("");
		this.$right.html(`
			<div class="oi-head">
				<div><b>${e(lead.lead_name || lead.name)}</b>
					<div class="oi-meta">${e(lead.mobile_no || "")} ${e(lead.email || "")} · ${e(lead.status || "")}
					${(data.identities || []).map((i) => `<span class="oi-chan">${e(i.channel_type)}</span>`).join("")}</div></div>
				<a class="btn btn-default btn-sm" href="${e(data.crm_url)}" target="_blank" rel="noopener">${e(data.deal ? __("Mở Deal trong CRM") : __("Mở Lead trong CRM"))}</a>
			</div>
			<div class="oi-thread">${msgs || `<div class="oi-empty">${e(__("Chưa có tin nhắn."))}</div>`}</div>
			${this.compose_html(data)}`);
		this.bind_compose(data);
		if (draft) this.$right.find(".oi-text").val(draft);
		if (data.truncated) {
			this.$right.find(".oi-thread").prepend(`<div class="oi-empty">${e(__("Chỉ hiện các tin gần nhất."))}</div>`);
		}
		if (stick) {
			const t = this.$right.find(".oi-thread");
			t.scrollTop(t[0].scrollHeight);
		}
	}

	compose_html(data) {
		const e = frappe.utils.escape_html;
		const c = data.compose || {};
		const z = c.zalo;
		let html = '<div class="oi-compose">';
		if (z && c.can_write) {
			html += `<textarea class="form-control oi-text" rows="3" maxlength="6000" placeholder="${e(__("Nhập tin trả lời Zalo"))}" ${z.can_send ? "" : "disabled"}></textarea>
				<div class="oi-actions">
					<button class="btn btn-primary btn-sm oi-send">${e(__("Gửi qua Zalo"))}</button>
					${z.can_request_phone ? `<button class="btn btn-default btn-sm oi-phone">${e(__("Xin SĐT qua Zalo"))}</button>` : ""}
					${z.sandbox ? `<span class="oi-meta">${e(__("Chế độ thử: không gửi thật"))}</span>` : ""}
				</div>`;
			if (!z.can_send) html += `<div class="oi-error">${e(z.message || "")}</div>`;
			else if (z.paid) html += `<div class="oi-warn">${e(z.message)}</div>`;
		}
		if (c.email) {
			html += `<div class="oi-actions"><a class="btn btn-default btn-sm" href="${e(data.crm_url)}" target="_blank" rel="noopener">${e(__("Trả lời email trong CRM"))}</a></div>`;
		}
		return html + "</div>";
	}

	bind_compose(data) {
		const $r = this.$right;
		$r.find(".oi-send").on("click", () => this.send($r.find(".oi-text").val(), 0));
		$r.find(".oi-phone").on("click", () => this.ask_phone(0));
	}

	send(text, confirm_paid) {
		const lead = this.current;
		if (!(text || "").trim()) {
			frappe.show_alert({ message: __("Nội dung tin nhắn đang trống"), indicator: "orange" });
			return;
		}
		this.$right.find(".oi-send").prop("disabled", true); // chống bấm hai lần
		frappe
			.call({
				method: "omni_crm.api.inbox.send_reply",
				args: { lead, channel: "Zalo OA", text, confirm_paid },
				freeze: false,
			})
			.then((r) => {
				const res = r.message || {};
				if (res.status === "confirm_required") {
					frappe.confirm(
						frappe.utils.escape_html(res.warning) + "<br>" + __("Bạn vẫn muốn gửi?"),
						() => this.send(text, 1),
						() => this.load_thread(lead, true)
					);
					return;
				}
				if (res.status === "Blocked") {
					frappe.show_alert({ message: frappe.utils.escape_html(res.reason), indicator: "red" }, 8);
				}
				this.load_thread(lead);
			})
			.catch(() => this.load_thread(lead, true));
	}

	ask_phone(confirm_paid) {
		const lead = this.current;
		this.$right.find(".oi-phone").prop("disabled", true);
		frappe
			.call({ method: "omni_crm.api.inbox.request_phone", args: { lead, confirm_paid }, freeze: false })
			.then((r) => {
				const res = r.message || {};
				if (res.status === "confirm_required") {
					frappe.confirm(
						frappe.utils.escape_html(res.warning) + "<br>" + __("Bạn vẫn muốn gửi?"),
						() => this.ask_phone(1),
						() => this.load_thread(lead, true)
					);
					return;
				}
				frappe.show_alert({ message: __("Đã gửi yêu cầu chia sẻ số điện thoại"), indicator: "green" });
				this.load_thread(lead);
			})
			.catch(() => this.load_thread(lead, true));
	}
}
