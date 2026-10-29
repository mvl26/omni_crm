frappe.ui.form.on("Omni Channel", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.channel_type !== "Zalo OA") return;
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
	},
});
