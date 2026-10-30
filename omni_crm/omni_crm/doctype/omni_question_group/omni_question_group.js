// Nhóm câu hỏi: nút bật/tắt bot tự gửi (D3-05). Chỉ Omni Admin thấy. Bật cần hộp xác nhận nêu rõ số liệu 14 ngày;
// chưa đạt điều kiện thì chỉ "bật cưỡng bức" kèm lý do (vẫn bị chặn bởi khóa hạ tầng và nhóm luôn chuyển người).
frappe.ui.form.on("Omni Question Group", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role("Omni Admin")) return;
		const e = frappe.utils.escape_html;
		if (frm.doc.auto_send_enabled) {
			frm.add_custom_button(__("Tắt tự động gửi"), () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Lý do") }],
					(v) =>
						frappe
							.call({ method: "omni_crm.api.auto.disable_auto", args: { group: frm.doc.name, reason: v.reason }, freeze: true })
							.then(() => frm.reload_doc()),
					__("Tắt tự động gửi")
				);
			});
			return;
		}
		if (frm.doc.always_handoff) return;
		frm.add_custom_button(__("Bật tự động gửi"), () => {
			frappe.call({ method: "omni_crm.api.auto.get_auto_status", args: { group: frm.doc.name } }).then((r) => {
				const s = r.message;
				const el = s.eligibility;
				const lines = [
					`${e(__("Mẫu (nháp đã quyết định, {0} ngày)", [el.window_days]))}: <b>${el.decided}</b> / ${e(__("tối thiểu"))} ${el.min_decided}`,
					`${e(__("Gửi nguyên văn hoặc sửa nhẹ"))}: <b>${el.good}</b> (${el.rate == null ? "-" : el.rate}% / ${e(__("cần"))} ${el.min_rate}%)`,
					`${e(__("Nghiêm trọng / chấm Sai kèm ghi chú"))}: <b>${el.critical}</b> / <b>${el.serious_wrong}</b>`,
					`${e(__("Đang chờ / hết hạn (không tính)"))}: ${el.pending} / ${el.expired}`,
				];
				if (!s.infra_allowed) lines.push(`<b class="text-danger">${e(__("Khóa hạ tầng omni_auto_send_allowed đang đóng: không bật được."))}</b>`);
				if (!el.eligible) lines.push(`<b class="text-danger">${e(__("Chưa đủ điều kiện"))}:</b> ${el.reasons.map(e).join("; ")}`);
				const go = (force, reason) =>
					frappe
						.call({ method: "omni_crm.api.auto.enable_auto", args: { group: frm.doc.name, confirm: 1, force: force ? 1 : 0, reason }, freeze: true })
						.then(() => frm.reload_doc());
				const d = new frappe.ui.Dialog({
					title: __("Bật bot tự gửi cho nhóm {0}", [frm.doc.name]),
					fields: [
						{ fieldtype: "HTML", options: `<p>${e(__("Bot sẽ TỰ GỬI câu trả lời cho khách thật trong nhóm này (trong khung miễn phí Zalo, giờ bot hoạt động). Có thể tắt ngay bất cứ lúc nào."))}</p><p>${lines.join("<br>")}</p>` },
						...(el.eligible ? [] : [{ fieldname: "reason", fieldtype: "Small Text", label: __("Lý do bật dù chưa đủ điều kiện (ghi lại)") }]),
					],
					primary_action_label: el.eligible ? __("Xác nhận bật") : __("Bật cưỡng bức"),
					primary_action(v) {
						d.hide();
						go(!el.eligible, v.reason);
					},
				});
				d.show();
			});
		});
	},
});
