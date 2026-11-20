// Kho ảnh S3 (MinIO) cho ảnh bài marketing (ADR 0033)
frappe.ui.form.on("Omni Media Storage", {
	refresh(frm) {
		frm.add_custom_button(__("Kiểm kho ảnh S3"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Lưu trước khi kiểm"));
				return;
			}
			frappe
				.call({ method: "omni_crm.api.media.check_storage", freeze: true })
				.then((r) => {
					frappe.msgprint({
						title: __("Kho ảnh S3 dùng được"),
						indicator: "green",
						message: __("Đã ghi, đọc, xoá một tệp thử trong bucket {0}.", [r.message.bucket]),
					});
				});
		});
	},
});
