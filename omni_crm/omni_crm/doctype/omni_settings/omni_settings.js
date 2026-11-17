// Nút kiểm kho ảnh S3 (MinIO, ADR 0030): ghi, đọc, xóa một object thử bằng cấu hình đã LƯU.
frappe.ui.form.on("Omni Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Kiểm kho ảnh S3"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Lưu cấu hình trước khi kiểm"));
				return;
			}
			frappe
				.call({ method: "omni_crm.api.media.check_storage", freeze: true })
				.then((r) => {
					const m = r.message || {};
					frappe.msgprint(
						__("Kho ảnh S3 dùng được: bucket {0}", [frappe.utils.escape_html(m.bucket || "")]),
						__("Đạt")
					);
				});
		});
	},
});
