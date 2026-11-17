// Bài đăng (ADR 0029): nút đổi trạng thái, chép link có UTM, số Lead và Deal của bài.
const OMNI_POST_API = "omni_crm.api.content_posts.";

frappe.ui.form.on("Omni Content Post", {
	refresh(frm) {
		const counts = (frm.doc.__onload || {}).counts;
		if (!frm.is_new() && counts) {
			frm.dashboard.add_indicator(__("Lead: {0}", [counts.leads]), counts.leads ? "green" : "gray");
			frm.dashboard.add_indicator(__("Deal: {0}", [counts.deals]), counts.deals ? "green" : "gray");
		}
		const off = (frm.doc.__onload || {}).auto_publish_off;
		if (off && ["Nháp", "Đã lên lịch"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(
				__("Bài tự động sẽ chưa được đăng: {0}", [frappe.utils.escape_html(off)]),
				"orange"
			);
		}
		if (frm.doc.last_error && ["Lỗi", "Cần kiểm", "Bỏ lỡ"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.last_error), "red");
		}
		if (frm.doc.utm_link) {
			frm.add_custom_button(__("Chép link có UTM"), () => {
				frappe.utils.copy_to_clipboard(frm.doc.utm_link);
			});
		}
		if (frm.is_new() || !frm.perm[0] || !frm.perm[0].write) return;
		const call = (method, args, label) =>
			frappe
				.call({ method: OMNI_POST_API + method, args: Object.assign({ name: frm.doc.name }, args || {}), freeze: true })
				.then(() => {
					if (label) frappe.show_alert({ message: label, indicator: "green" });
					frm.reload_doc();
				});
		const status = frm.doc.status;
		if (["Nháp", "Lỗi", "Bỏ lỡ"].includes(status)) {
			frm.add_custom_button(__("Lên lịch"), () => {
				if (frm.is_dirty()) {
					frappe.msgprint(__("Lưu bài trước khi lên lịch"));
					return;
				}
				call("schedule", {}, __("Đã lên lịch"));
			}).addClass("btn-primary");
		}
		if (status === "Đã lên lịch") {
			frm.add_custom_button(__("Huỷ lịch"), () => call("unschedule", {}, __("Bài về Nháp")));
		}
		if (["Nháp", "Đã lên lịch", "Lỗi", "Bỏ lỡ"].includes(status)) {
			frm.add_custom_button(
				__("Huỷ bài"),
				() => frappe.confirm(__("Huỷ bài này? Bài sẽ không được đăng."), () => call("cancel", {}, __("Đã huỷ bài"))),
				__("Khác")
			);
		}
		if (status === "Đã huỷ") {
			frm.add_custom_button(__("Mở lại"), () => call("reopen", {}, __("Bài về Nháp")));
		}
		if (status === "Cần kiểm") {
			frm.add_custom_button(__("Bài đã lên Page"), () => {
				frappe.prompt(
					{ fieldname: "post_url", fieldtype: "Data", options: "URL", label: __("Link bài trên Page"), reqd: 1 },
					(v) => call("resolve_uncertain", { published: 1, post_url: v.post_url }, __("Bài Đã đăng")),
					__("Bài đã lên Page")
				);
			}, __("Đã kiểm Page"));
			frm.add_custom_button(
				__("Bài chưa lên Page"),
				() => call("resolve_uncertain", { published: 0 }, __("Bài chuyển sang Lỗi")),
				__("Đã kiểm Page")
			);
		}
	},
});
