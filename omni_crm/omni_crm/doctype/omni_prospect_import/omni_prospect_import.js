// Nhật ký nhập danh sách nhà thầu (V2-03): Chạy thử / Nhập / Tải mẫu trống. Chỉ Omni Admin thấy nút (server kiểm lại).
frappe.ui.form.on("Omni Prospect Import", {
	refresh(frm) {
		if (frm.doc.__onload && frm.doc.__onload.report_html) {
			frm.fields_dict.report_html.$wrapper.html(frm.doc.__onload.report_html);
		}
		frm.add_custom_button(__("Tải mẫu trống"), () => {
			window.open("/api/method/omni_crm.api.prospect.download_template");
		});
		if (frm.is_new() || frm.doc.status === "Running" || frm.doc.status === "Queued") return;
		if (!frm.doc.file) return;
		const run = (dry_run) =>
			frappe.call({
				method: "omni_crm.api.prospect.run_import",
				args: { log: frm.doc.name, dry_run: dry_run ? 1 : 0 },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Đã đưa vào hàng đợi, tải lại sau ít phút"), indicator: "blue" });
					frm.reload_doc();
				},
			});
		frm.add_custom_button(__("Chạy thử"), () => run(true), __("Nhập"));
		frm.add_custom_button(__("Nhập thật"), () => {
			frappe.confirm(__("Nhập thật vào CRM? Nên chạy thử và xem báo cáo trước."), () => run(false));
		}, __("Nhập"));
	},
});
