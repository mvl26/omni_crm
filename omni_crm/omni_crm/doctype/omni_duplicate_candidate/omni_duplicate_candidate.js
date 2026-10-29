frappe.ui.form.on("Omni Duplicate Candidate", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Open") return;
		const call = (method, args) =>
			frappe.call({ method: `omni_crm.identity.duplicates.${method}`, args }).then(() => frm.reload_doc());
		for (const lead of [frm.doc.lead_a, frm.doc.lead_b]) {
			frm.add_custom_button(
				__("Gộp vào {0}", [lead]),
				() =>
					frappe.confirm(__("Gộp Lead còn lại vào {0}? Lead còn lại sẽ được giữ nhưng đánh dấu đã gộp.", [lead]), () =>
						call("merge", { candidate: frm.doc.name, primary: lead })
					),
				__("Gộp")
			);
		}
		frm.add_custom_button(__("Không trùng"), () => call("dismiss", { candidate: frm.doc.name }));
	},
});
