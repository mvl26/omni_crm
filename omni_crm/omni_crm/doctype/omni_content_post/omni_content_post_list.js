// Màu trạng thái bài đăng trong danh sách (ADR 0029).
frappe.listview_settings["Omni Content Post"] = {
	add_fields: ["status", "publish_mode"],
	get_indicator(doc) {
		const colors = {
			"Nháp": "gray",
			"Đã lên lịch": "blue",
			"Đang đăng": "purple",
			"Đã đăng": "green",
			"Lỗi": "red",
			"Cần kiểm": "orange",
			"Bỏ lỡ": "orange",
			"Đã huỷ": "darkgrey",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
