frappe.query_reports["Bot nháp"] = {
	filters: [
		{
			fieldname: "view",
			label: __("Cách xem"),
			fieldtype: "Select",
			options: "Theo nhóm\nTheo tuần",
			default: "Theo nhóm",
		},
		{ fieldname: "from_date", label: __("Từ ngày"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Đến ngày"), fieldtype: "Date" },
	],
};
