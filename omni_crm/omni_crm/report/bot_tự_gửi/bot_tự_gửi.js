frappe.query_reports["Bot tự gửi"] = {
	filters: [
		{ fieldname: "from_date", label: __("Từ ngày (tin bot đã gửi)"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Đến ngày"), fieldtype: "Date" },
	],
};
