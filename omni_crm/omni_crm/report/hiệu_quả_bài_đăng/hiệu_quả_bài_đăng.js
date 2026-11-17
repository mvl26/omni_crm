// Báo cáo "Hiệu quả bài đăng" (ADR 0029): theo bài, lọc theo chiến dịch và giờ đăng.
frappe.query_reports["Hiệu quả bài đăng"] = {
	filters: [
		{ fieldname: "campaign", label: __("Chiến dịch"), fieldtype: "Link", options: "Omni Campaign" },
		{ fieldname: "from_date", label: __("Giờ đăng từ ngày"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Đến ngày"), fieldtype: "Date" },
		{
			fieldname: "status",
			label: __("Trạng thái"),
			fieldtype: "Select",
			options: "\nNháp\nĐã lên lịch\nĐang đăng\nĐã đăng\nLỗi\nCần kiểm\nBỏ lỡ\nĐã huỷ",
		},
	],
};
