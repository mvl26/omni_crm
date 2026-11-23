// Báo cáo "Kho ảnh MinIO" (ADR 0033): ảnh và tệp đính kèm công khai của bài marketing trên kho S3, chỉ đọc
frappe.query_reports["Kho ảnh MinIO"] = {
	filters: [
		{
			fieldname: "status",
			label: __("Trạng thái"),
			fieldtype: "Select",
			options: "\nKhớp\nThiếu trên MinIO\nMồ côi",
		},
		{
			fieldname: "content_item",
			label: __("Bài"),
			fieldtype: "Link",
			options: "Omni Content Item",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "view_url") {
			// chỉ địa chỉ /omni-media/... do máy chủ trả (đã kiểm ở Python)
			if (!value || !String(value).startsWith("/omni-media/")) return "";
			return `<a href="${encodeURI(value)}" target="_blank" rel="noopener">${__("Mở")}</a>`;
		}
		const out = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const color = { "Khớp": "green", "Thiếu trên MinIO": "red", "Mồ côi": "orange" }[data.status];
			return color ? `<span class="indicator-pill ${color}">${out}</span>` : out;
		}
		return out;
	},
};
