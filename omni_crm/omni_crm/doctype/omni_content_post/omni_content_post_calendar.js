// Lịch đăng theo tháng (ADR 0029): mỗi bài một ô theo Giờ đăng, màu theo trạng thái.
frappe.views.calendar["Omni Content Post"] = {
	field_map: {
		start: "publish_at",
		end: "publish_end",
		id: "name",
		title: "title",
		allDay: 0,
		status: "status",
		editable: "editable", // máy chủ trả 0: không kéo thả đổi giờ trên lịch, đổi Giờ đăng trên form
	},
	get_events_method: "omni_crm.api.content_posts.calendar_events",
	get_css_class(d) {
		const colors = {
			"Nháp": "gray",
			"Đã lên lịch": "blue",
			"Đang đăng": "purple",
			"Đã đăng": "green",
			"Lỗi": "red",
			"Cần kiểm": "orange",
			"Bỏ lỡ": "orange",
			"Đã huỷ": "gray",
		};
		return colors[d.status] || "blue";
	},
	filters: [
		{ fieldtype: "Link", fieldname: "campaign", options: "Omni Campaign", label: __("Chiến dịch") },
	],
};
