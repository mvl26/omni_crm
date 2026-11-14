const OMNI_META_CONNECT = "omni_crm.api.meta_connect";

frappe.ui.form.on("Omni Channel", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.channel_type === "Zalo OA") omni_setup_zalo(frm);
		if (frm.doc.channel_type === "Meta Messenger") omni_setup_facebook_page(frm);
	},
});

function omni_setup_zalo(frm) {
	frm.add_custom_button(__("Kết nối Zalo OA"), () => {
		frappe.confirm(__("Chuyển sang trang Zalo để cấp quyền cho OA?"), () => {
			window.location.href =
				"/api/method/omni_crm.api.webhooks.zalo_oauth_start?channel=" +
				encodeURIComponent(frm.doc.name);
		});
	});
	if (frm.doc.zalo_token_expires_at) {
		frm.dashboard.set_headline(
			__("Token Zalo hết hạn lúc {0}", [frappe.datetime.str_to_user(frm.doc.zalo_token_expires_at)])
		);
	}
}

// ADR 0028: nút "Connect Facebook Page" (Facebook Login for Business) rồi chọn Page để đăng ký webhook.
function omni_setup_facebook_page(frm) {
	frm.add_custom_button(__("Connect Facebook Page"), () => {
		window.location.href =
			"/api/method/" + OMNI_META_CONNECT + ".start?channel=" + encodeURIComponent(frm.doc.name);
	});
	if (frm.doc.meta_connected_at) {
		frm.dashboard.set_headline(
			__("Connected to {0} · Webhook fields: {1}", [
				frappe.utils.escape_html(frm.doc.meta_page_name || frm.doc.meta_page_id || ""),
				frappe.utils.escape_html(frm.doc.meta_subscribed_fields || "-"),
			]),
			"green"
		);
	}
	const { session, error } = omni_take_connect_params();
	if (error) {
		frappe.msgprint(__("The Facebook login was cancelled. No Page was connected."));
	} else if (session) {
		omni_choose_page(frm, session);
	}
}

// Đọc rồi xóa tham số callback khỏi URL và route_options để F5 hay mở form khác không chạy lại.
function omni_take_connect_params() {
	const params = new URLSearchParams(window.location.search);
	const opts = frappe.route_options || {};
	const session = params.get("fb_connect") || opts.fb_connect;
	const error = params.get("fb_connect_error") || opts.fb_connect_error;
	if (session || error) {
		params.delete("fb_connect");
		params.delete("fb_connect_error");
		const rest = params.toString();
		window.history.replaceState(
			window.history.state,
			"",
			window.location.pathname + (rest ? "?" + rest : "")
		);
	}
	if (frappe.route_options) {
		delete frappe.route_options.fb_connect;
		delete frappe.route_options.fb_connect_error;
	}
	return { session, error };
}

function omni_choose_page(frm, session) {
	frappe
		.call({
			method: OMNI_META_CONNECT + ".pages",
			args: { session },
			freeze: true,
			freeze_message: __("Loading your Facebook Pages..."),
		})
		.then((r) => {
			const data = r.message || {};
			const pages = data.pages || [];
			if (!pages.length) {
				frappe.msgprint(
					__("Facebook returned no Pages. Click Connect Facebook Page again and select your Page.")
				);
				return;
			}
			const current = pages.find((p) => p.id === data.current_page_id);
			const dialog = new frappe.ui.Dialog({
				title: __("Select the Facebook Page to connect"),
				fields: [
					{
						fieldname: "page_id",
						fieldtype: "Select",
						label: __("Facebook Page"),
						options: pages.map((p) => ({ label: `${p.name} (${p.id})`, value: p.id })),
						default: (current || pages[0]).id,
						reqd: 1,
					},
					{
						fieldname: "help",
						fieldtype: "HTML",
						options: `<p class="text-muted small">${__(
							"Miyano CRM subscribes this Page to webhooks, so messages that customers send to the Page appear in the Messenger tab of their Lead."
						)}</p>`,
					},
				],
				primary_action_label: __("Subscribe Page"),
				primary_action(values) {
					dialog.hide();
					omni_subscribe_page(frm, session, values.page_id);
				},
			});
			dialog.show();
		});
}

function omni_subscribe_page(frm, session, page_id) {
	frappe
		.call({
			method: OMNI_META_CONNECT + ".subscribe",
			args: { session, page_id },
			freeze: true,
			freeze_message: __("Subscribing the Page to webhooks..."),
		})
		.then((r) => {
			const out = r.message || {};
			const esc = frappe.utils.escape_html;
			const missing = out.missing || [];
			const fields = (out.fields || []).map((f) => `<code>${esc(f)}</code>`).join(", ");
			let message =
				`<p>${__("Facebook Page")}: <b>${esc(out.page_name || "")}</b> (${esc(out.page_id || "")})</p>` +
				`<p>${__("Webhook fields")}: ${fields || "-"}</p>`;
			if (missing.length) {
				message += `<p class="text-danger">${__("Webhook fields not subscribed")}: ${missing.map(esc).join(", ")}</p>`;
			}
			frappe.msgprint({
				title: __("Facebook Page connected"),
				indicator: missing.length ? "orange" : "green",
				message,
			});
			frm.reload_doc();
		});
}
