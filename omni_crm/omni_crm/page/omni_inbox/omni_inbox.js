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
		this.draft_edit = null; // {name, text}: nháp đang được sửa (giữ khi làm mới)
		this.rate_pending = null; // {lead, draft}: nháp vừa gửi/bỏ, hỏi nhanh Đúng/Sai/Thiếu
		const e = frappe.utils.escape_html;
		$(page.body).html(`
			<div class="oi-system-status" style="display:none"></div>
			<div class="omni-inbox">
				<div class="oi-left">
					<div class="oi-filters">
						<input type="text" class="form-control oi-search" placeholder="${e(__("Tìm theo tên, SĐT, email, đơn vị"))}" style="flex:1;min-width:120px">
						<select class="form-control oi-channel" style="width:auto">
							<option value="">${e(__("Mọi kênh"))}</option>
							<option value="Zalo OA">Zalo OA</option>
							<option value="Email">Email</option>
							<option value="Telephony">${e(__("Điện thoại"))}</option>
						</select>
						<label><input type="checkbox" class="oi-unanswered"> ${e(__("Chưa trả lời"))}</label>
						<label><input type="checkbox" class="oi-mine"> ${e(__("Của tôi"))}</label>
						<label><input type="checkbox" class="oi-calls"> ${e(__("Cuộc gọi"))}</label>
						<label><input type="checkbox" class="oi-hasdraft"> ${e(__("Có nháp bot"))}</label>
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
		$(page.body).find(".oi-channel, .oi-unanswered, .oi-mine, .oi-calls, .oi-hasdraft").on("change", () => this.load_list());
		this.on_update_handler = (data) => this.on_update(data);
		this.active = false;
		this.$status = $(page.body).find(".oi-system-status");
		this.resume();
		this.load_list();
		this.load_status();
	}

	// Khối "Tình trạng hệ thống": chỉ hiện khi có sự cố kênh; thông điệp cố định từ máy chủ.
	load_status() {
		frappe
			.call({ method: "omni_crm.api.inbox.system_status", freeze: false, silent: true })
			.then((r) => {
				const msgs = (r.message || {}).messages || [];
				const e = frappe.utils.escape_html;
				this.$status
					.html(msgs.map((m) => `<div>${e(m)}</div>`).join(""))
					.toggle(msgs.length > 0);
			});
	}

	resume() {
		if (this.active) return;
		this.active = true;
		// Realtime: payload không chứa nội dung tin; nhận tin hiệu rồi gọi lại API có kiểm quyền.
		frappe.realtime.on("omni_inbox_update", this.on_update_handler);
		// Dự phòng khi mất kết nối realtime.
		this.poll = setInterval(() => {
			this.load_list(true);
			this.load_status();
		}, 60000);
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
			channel: b.find(".oi-calls").is(":checked") ? "Telephony" : b.find(".oi-channel").val(),
			has_draft: b.find(".oi-hasdraft").is(":checked") ? 1 : 0,
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
					<span>${r.draft_pending ? '<span class="oi-chan oi-draftchip" title="' + e(__("Có nháp bot chờ duyệt")) + '">' + e(__("Nháp")) + "</span> " : ""}${r.unanswered ? '<span class="oi-dot" title="' + e(__("Chưa trả lời")) + '"></span>' : ""}${e(r.lead_name)}${r.organization ? ' <span class="oi-meta">· ' + e(r.organization) + "</span>" : ""}${r.fit_tier ? ' <span class="oi-chan" title="' + e(__("Mức phù hợp")) + '">' + e(__("Hạng")) + " " + e(r.fit_tier) + "</span>" : ""}</span>
					<span class="oi-time">${e(frappe.datetime.prettyDate(r.last_at))}</span>
				</div>
				<div class="oi-snippet">${r.last_direction === "out" ? e(__("Bạn")) + ": " : ""}${e(r.last_text || "")}</div>
				<div>${r.priority_label ? '<span class="oi-chan" style="color:#b42318">' + e(r.priority_label) + "</span>" : ""}${r.unrecorded ? '<span class="oi-chan" style="color:#b42318">' + e(__("Chưa ghi kết quả")) + "</span>" : ""}${(r.channels || []).map((c) => `<span class="oi-chan">${e(c)}</span>`).join("")}
					<span class="oi-meta">${e(r.lead_owner || "")}</span></div>
			</div>`
				)
				.join("")
		);
		this.$list.find(".oi-item").on("click", (ev) => this.show($(ev.currentTarget).data("lead")));
	}

	show(lead) {
		if (!lead) return;
		if (lead !== this.current) {
			this.draft_edit = null;
			this.rate_pending = null;
		}
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
				const botchip = m.bot_sent ? ` · <span class="oi-chan oi-botsent-chip">${e(__("Bot đã gửi"))}</span>` : "";
				return `<div class="oi-msg ${m.direction}">
					<div class="oi-meta"><b>${e(m.channel_type || "")}</b> · ${e(m.sender || "")} · ${e(frappe.datetime.str_to_user(m.at))}${flag}${sandbox}${botchip}</div>${why}
					<div>${e(m.text).replace(/\n/g, "<br>")}</div>${atts}${this.botsent_html(m)}</div>`;
			})
			.join("");
		this.$right.html(`
			<div class="oi-head">
				<div><b>${e(lead.lead_name || lead.name)}</b>${data.profile && data.profile.organization ? ` <span class="oi-meta">· ${e(data.profile.organization)}</span>${data.profile.fit_tier ? ` <span class="oi-chan">${e(__("Hạng"))} ${e(data.profile.fit_tier)}</span>` : ""}` : ""}
					<div class="oi-meta">${data.profile && data.profile.person_refused ? e(__("Từ chối nhận liên hệ")) : e(lead.mobile_no || "") + " " + e(lead.email || "")} · ${e(lead.status || "")}
					${data.profile && data.profile.next_step ? " · " + e(__("Bước tiếp theo")) + ": " + e(data.profile.next_step.action) + " (" + e(data.profile.next_step.due) + ")" : ""}
					${data.profile && data.profile.unrecorded ? ' <span class="oi-chan" style="color:#b42318">' + e(__("Chưa ghi kết quả")) + "</span>" : ""}
					${(data.identities || []).map((i) => `<span class="oi-chan">${e(i.channel_type)}</span>`).join("")}
					${this.score_html(data.score)}</div></div>
				<span><a class="btn btn-default btn-sm" href="/app/omni-customer/${encodeURIComponent(lead.name)}">${e(__("Mở hồ sơ chăm sóc"))}</a>
				<a class="btn btn-default btn-sm" href="${e(data.crm_url)}" target="_blank" rel="noopener">${e(data.deal ? __("Mở Deal trong CRM") : __("Mở Lead trong CRM"))}</a></span>
			</div>
			${this.pause_html(data.bot)}
			${this.bot_html(lead)}
			<div class="oi-thread">${msgs || `<div class="oi-empty">${e(__("Chưa có tin nhắn."))}</div>`}${this.drafts_html(data)}</div>
			${this.compose_html(data)}`);
		this.bind_compose(data);
		this.bind_drafts(data);
		this.bind_botsent();
		this.bind_pause();
		if (draft) this.$right.find(".oi-text").val(draft);
		if (data.truncated) {
			this.$right.find(".oi-thread").prepend(`<div class="oi-empty">${e(__("Chỉ hiện các tin gần nhất."))}</div>`);
		}
		if (stick) {
			const t = this.$right.find(".oi-thread");
			t.scrollTop(t[0].scrollHeight);
		}
	}

	// Tạm dừng bot (D3-02): nhãn khi bot đang im với khách này, nút Cho bot tiếp tục / Tạm dừng. Mọi chuỗi thoát HTML.
	pause_html(bot) {
		if (!bot) return "";
		const e = frappe.utils.escape_html;
		if (bot.paused_until) {
			const btn = bot.can_control
				? ` <button class="btn btn-default btn-xs oi-bot-resume">${e(__("Cho bot tiếp tục"))}</button>`
				: "";
			return `<div class="oi-bot oi-botpause"><b>${e(__("Bot đang tạm dừng đến"))} ${e(frappe.datetime.str_to_user(bot.paused_until))}</b>${btn}</div>`;
		}
		if (bot.can_control && bot.mode && bot.mode !== "Off") {
			return `<div class="oi-bot oi-botpause"><button class="btn btn-default btn-xs oi-bot-pause">${e(__("Tạm dừng bot với khách này"))}</button></div>`;
		}
		return "";
	}

	bind_pause() {
		const lead = this.current;
		const call = (method) => {
			this.$right.find(".oi-botpause button").prop("disabled", true); // chống bấm hai lần
			frappe
				.call({ method, args: { lead }, freeze: false })
				.finally(() => this.load_thread(lead, true));
		};
		this.$right.find(".oi-bot-resume").on("click", () => call("omni_crm.api.bot_control.resume_bot"));
		this.$right.find(".oi-bot-pause").on("click", () => call("omni_crm.api.bot_control.pause_bot"));
	}

	score_html(score) {
		// Điểm Lead do quy tắc cố định (không phải AI); rê chuột thấy lý do. Luôn thoát HTML.
		if (!score) return "";
		const e = frappe.utils.escape_html;
		const why = (score.reasons || []).join("\n") || __("Chưa có tín hiệu nào được cộng điểm");
		return `<span class="oi-chan oi-score ${score.hot ? "oi-hot" : ""}" title="${e(__("Điểm Lead") + ": " + score.score + "\n" + why)}">${e(__("Điểm"))} ${e(score.score)}</span>`;
	}

	bot_html(lead) {
		// Tóm tắt và nhãn ý định do bot ghi: luôn thoát HTML (nội dung có nguồn từ lời khách).
		const e = frappe.utils.escape_html;
		if (!lead.omni_bot_summary && !lead.omni_intent) return "";
		const intent = lead.omni_intent ? `<span class="oi-chan oi-intent">${e(lead.omni_intent)}</span> ` : "";
		return `<div class="oi-bot">${intent}${e(lead.omni_bot_summary || "")}
			<span class="oi-meta">${e(__("Tóm tắt do bot, có thể chưa chính xác"))}</span></div>`;
	}

	// Nháp của bot (D3-01): nằm ngay dưới tin khách cuối, luôn ghi rõ "chưa gửi"; mọi chuỗi từ máy chủ đều thoát HTML.
	drafts_html(data) {
		const e = frappe.utils.escape_html;
		const cards = (data.drafts || []).map((d) => {
			const kb = (d.kb || [])
				.map((k) => `<a class="oi-kb" href="#" data-draft="${e(d.name)}" data-item="${e(k.id)}" title="${e(k.question)}">${e(k.id)}</a>`)
				.join(" ");
			const head = `<div class="oi-meta"><b>${e(__("Nháp của bot — chưa gửi"))}</b>${d.question_group ? " · " + e(__("Nhóm") + ": " + d.question_group) : ""}</div>`;
			const src = kb ? `<div class="oi-meta">${e(__("Nguồn kho tri thức"))}: ${kb}</div>` : "";
			if (d.action === "Handoff") {
				return `<div class="oi-draft oi-draft-handoff" data-draft="${e(d.name)}">${head}
					<div><b>${e(__("Bot đề nghị chuyển người"))}</b>: ${e(d.handoff_reason || "")}</div>${src}
					<div class="oi-actions">${d.can_decide ? `<button class="btn btn-default btn-sm oi-draft-discard">${e(__("Bỏ"))}</button>` : ""}
					<span class="oi-meta">${e(__("Không có nội dung để gửi"))}</span></div></div>`;
			}
			const editing = this.draft_edit && this.draft_edit.name === d.name;
			const body = editing
				? `<textarea class="form-control oi-draft-text" rows="4" maxlength="6000">${e(this.draft_edit.text)}</textarea>`
				: `<div class="oi-draft-body">${e(d.text || "").replace(/\n/g, "<br>")}</div>`;
			let actions = "";
			if (d.can_send && editing) {
				actions = `<button class="btn btn-primary btn-sm oi-draft-send-edited">${e(__("Gửi bản đã sửa"))}</button>
					<button class="btn btn-default btn-sm oi-draft-cancel-edit">${e(__("Hủy sửa"))}</button>`;
			} else if (d.can_send) {
				actions = `<button class="btn btn-primary btn-sm oi-draft-send">${e(__("Gửi"))}</button>
					<button class="btn btn-default btn-sm oi-draft-edit">${e(__("Sửa rồi gửi"))}</button>
					<button class="btn btn-default btn-sm oi-draft-discard">${e(__("Bỏ"))}</button>`;
			} else if (d.can_decide) {
				actions = `<button class="btn btn-default btn-sm oi-draft-discard">${e(__("Bỏ"))}</button>
					<span class="oi-meta">${e(__("Kênh này trả lời trong CRM, không gửi từ Hộp thư"))}</span>`;
			}
			return `<div class="oi-draft" data-draft="${e(d.name)}">${head}${body}${src}<div class="oi-actions">${actions}</div></div>`;
		});
		let rate = "";
		const rp = this.rate_pending;
		if (rp && rp.lead === this.current) {
			rate = `<div class="oi-draft oi-rate" data-draft="${e(rp.draft)}"><span>${e(__("Nháp này"))}:</span>
				<button class="btn btn-default btn-xs oi-rate-btn" data-rating="Đúng">${e(__("Đúng"))}</button>
				<button class="btn btn-default btn-xs oi-rate-btn" data-rating="Sai">${e(__("Sai"))}</button>
				<button class="btn btn-default btn-xs oi-rate-btn" data-rating="Thiếu">${e(__("Thiếu"))}</button>
				<a href="#" class="oi-rate-skip oi-meta">${e(__("Bỏ qua"))}</a></div>`;
		}
		return cards.join("") + rate;
	}

	// Tin bot đã tự gửi (D3-05): chấm Đúng/Sai/Thiếu và nút "Tắt tự động nhóm này" (Omni Admin). Mọi chuỗi thoát HTML.
	botsent_html(m) {
		const b = m.bot_sent ? m.bot : null;
		if (!b) return "";
		const e = frappe.utils.escape_html;
		let rate = "";
		if (b.rating) {
			rate = `<span class="oi-meta">${e(__("Đã chấm"))}: ${e(b.rating)}</span>`;
		} else if (b.can_rate) {
			rate = ["Đúng", "Sai", "Thiếu"]
				.map((r) => `<button class="btn btn-default btn-xs oi-botrate" data-rating="${e(r)}">${e(__(r))}</button>`)
				.join(" ");
		}
		const off = b.can_disable
			? ` <button class="btn btn-danger btn-xs oi-botoff" data-group="${e(b.question_group || "")}">${e(__("Tắt tự động nhóm này"))}</button>`
			: "";
		return `<div class="oi-botsent" data-draft="${e(b.draft)}">${e(b.question_group || "")} ${rate}${off}</div>`;
	}

	bind_botsent() {
		const $r = this.$right;
		$r.find(".oi-botrate").on("click", (ev) => {
			const $b = $(ev.currentTarget);
			const draft = $b.closest(".oi-botsent").data("draft");
			const rating = $b.data("rating");
			$b.closest(".oi-botsent").find("button").prop("disabled", true); // chống bấm hai lần
			frappe
				.call({ method: "omni_crm.api.drafts.rate_draft", args: { draft, rating }, freeze: false })
				.then((r) => {
					const off = r.message && r.message.auto_disabled;
					frappe.show_alert(
						{ message: off ? __("Đã ghi nhận. Nhóm này đã tự tắt tự động gửi.") : __("Đã ghi nhận, cảm ơn"), indicator: off ? "orange" : "green" },
						6
					);
				})
				.finally(() => this.load_thread(this.current, true));
		});
		$r.find(".oi-botoff").on("click", (ev) => {
			const group = $(ev.currentTarget).data("group");
			const e = frappe.utils.escape_html;
			frappe.confirm(e(__("Tắt tự động gửi của nhóm {0}? Từ giờ bot chỉ soạn nháp cho sale duyệt.", [group])), () => {
				frappe
					.call({ method: "omni_crm.api.auto.disable_auto", args: { group }, freeze: true })
					.then(() => frappe.show_alert({ message: __("Đã tắt tự động gửi"), indicator: "green" }))
					.finally(() => this.load_thread(this.current, true));
			});
		});
	}

	bind_drafts(data) {
		const $r = this.$right;
		const name = (el) => $(el).closest(".oi-draft").data("draft");
		const draft_of = (n) => (data.drafts || []).find((d) => d.name === n) || {};
		$r.find(".oi-kb").on("click", (ev) => {
			ev.preventDefault();
			const $a = $(ev.currentTarget);
			frappe
				.call({
					method: "omni_crm.api.drafts.get_draft_kb_item",
					args: { draft: $a.data("draft"), item: $a.data("item") },
					freeze: false,
				})
				.then((r) => {
					const k = r.message || {};
					const e = frappe.utils.escape_html;
					frappe.msgprint({
						title: e(k.question || k.id || ""),
						message: e(k.answer || "").replace(/\n/g, "<br>"),
					});
				});
		});
		$r.find(".oi-draft-send").on("click", (ev) => this.send_draft(name(ev.currentTarget), null, 0));
		$r.find(".oi-draft-edit").on("click", (ev) => {
			const n = name(ev.currentTarget);
			this.draft_edit = { name: n, text: draft_of(n).text || "" };
			this.render_thread(data, true);
		});
		$r.find(".oi-draft-text").on("input", (ev) => {
			if (this.draft_edit) this.draft_edit.text = $(ev.currentTarget).val();
		});
		$r.find(".oi-draft-cancel-edit").on("click", () => {
			this.draft_edit = null;
			this.render_thread(data, true);
		});
		$r.find(".oi-draft-send-edited").on("click", (ev) => {
			const n = name(ev.currentTarget);
			const text = $r.find(".oi-draft-text").val();
			if (!(text || "").trim()) {
				frappe.show_alert({ message: __("Nội dung tin nhắn đang trống"), indicator: "orange" });
				return;
			}
			this.send_draft(n, text, 0);
		});
		$r.find(".oi-draft-discard").on("click", (ev) => this.discard_draft(name(ev.currentTarget)));
		$r.find(".oi-rate-btn").on("click", (ev) => {
			const n = name(ev.currentTarget);
			const rating = $(ev.currentTarget).data("rating");
			this.rate_pending = null;
			frappe
				.call({ method: "omni_crm.api.drafts.rate_draft", args: { draft: n, rating }, freeze: false })
				.then(() => frappe.show_alert({ message: __("Đã ghi nhận, cảm ơn"), indicator: "green" }))
				.finally(() => this.load_thread(this.current, true));
		});
		$r.find(".oi-rate-skip").on("click", (ev) => {
			ev.preventDefault();
			this.rate_pending = null;
			this.render_thread(data, true);
		});
	}

	send_draft(draft, text, confirm_paid) {
		const lead = this.current;
		this.$right.find(".oi-draft button").prop("disabled", true); // chống bấm hai lần
		frappe
			.call({
				method: "omni_crm.api.drafts.send_draft",
				// text=null (gửi nguyên văn) không được đưa vào args: jQuery gửi null thành chuỗi rỗng và máy chủ sẽ báo "trống".
				args: Object.assign({ draft, confirm_paid }, typeof text === "string" ? { text } : {}),
				freeze: false,
			})
			.then((r) => {
				const res = r.message || {};
				if (res.status === "confirm_required") {
					frappe.confirm(
						frappe.utils.escape_html(res.warning) + "<br>" + __("Bạn vẫn muốn gửi?"),
						() => this.send_draft(draft, text, 1),
						() => this.load_thread(lead, true)
					);
					return;
				}
				if (res.status === "Blocked") {
					frappe.show_alert({ message: frappe.utils.escape_html(res.reason), indicator: "red" }, 8);
				}
				if (res.status === "Queued") {
					this.draft_edit = null;
					this.rate_pending = { lead, draft };
				}
				this.load_thread(lead);
			})
			.catch(() => this.load_thread(lead, true));
	}

	discard_draft(draft) {
		const lead = this.current;
		this.$right.find(".oi-draft button").prop("disabled", true);
		frappe
			.call({ method: "omni_crm.api.drafts.discard_draft", args: { draft }, freeze: false })
			.then(() => {
				this.draft_edit = null;
				this.rate_pending = { lead, draft };
			})
			.finally(() => this.load_thread(lead));
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
			if (z.quota) html += `<div class="oi-meta ${z.quota.over ? "text-danger" : ""}">${e(z.quota.text)}</div>`;
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
