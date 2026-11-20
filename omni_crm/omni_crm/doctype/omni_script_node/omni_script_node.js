// Nút duyệt đề xuất của bộ não cải tiến (ADR 0033). Chỉ hiện ở ô đề xuất (mã DX_…) và ô từ vựng chờ duyệt.
frappe.ui.form.on("Omni Script Node", {
	refresh(frm) {
		if (frm.is_new()) return;
		const code = frm.doc.code || "";
		if (code.startsWith("DX_")) {
			frm.add_custom_button(__("Áp dụng đề xuất"), () => {
				frappe.confirm(__("Chép câu này vào ô thật và xoá ô đề xuất?"), () => {
					frappe.call({ method: "omni_crm.script.brain.approve", args: { name: frm.doc.name } }).then((r) => {
						frappe.show_alert({ message: __("Đã áp dụng vào ô {0}", [r.message.node]), indicator: "green" });
						frappe.set_route("Form", "Omni Script Node", r.message.node);
					});
				});
			}).addClass("btn-primary");
			frm.add_custom_button(__("Bỏ đề xuất"), () => {
				frappe.call({ method: "omni_crm.script.brain.reject", args: { name: frm.doc.name } }).then(() => {
					frappe.set_route("List", "Omni Script Node");
				});
			});
		}
		if (code === "TUVUNG_DEXUAT") {
			frm.add_custom_button(__("Duyệt các dòng còn lại"), () => {
				frappe.confirm(__("Xoá các dòng không nhận trước, lưu, rồi mới bấm. Duyệt các dòng còn lại?"), () => {
					frappe.call({ method: "omni_crm.script.brain.approve_phrases" }).then((r) => {
						frappe.show_alert({ message: __("Đã duyệt {0} cách nói", [r.message.approved]), indicator: "green" });
						frappe.set_route("Form", "Omni Script Node", "TUVUNG_KHACH");
					});
				});
			}).addClass("btn-primary");
		}
	},
});
