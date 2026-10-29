frappe.query_reports["Bot ngầm"] = {
	filters: [
		{
			fieldname: "view",
			label: __("Cách xem"),
			fieldtype: "Select",
			options: "Theo nhóm\nNháp khác câu sale nhất",
			default: "Theo nhóm",
		},
		{ fieldname: "from_date", label: __("Từ ngày"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Đến ngày"), fieldtype: "Date" },
		{ fieldname: "limit", label: __("Số nháp (cách xem 2)"), fieldtype: "Int", default: 10 },
	],
};
