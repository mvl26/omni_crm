// Bài marketing (V3-10): xem trước ảnh bìa và ảnh thêm ở đầu form Desk (ADR 0033, việc V5).
// Ô Attach Image của Desk chỉ hiện đường dẫn (ảnh chỉ bật lên khi rê chuột); dải ảnh này hiện cả ảnh /files/ lẫn
// ảnh đã chuyển sang kho S3 (/omni-media/...).
frappe.ui.form.on("Omni Content Item", {
	refresh(frm) {
		render_previews(frm);
	},
	media(frm) {
		render_previews(frm);
	},
});

frappe.ui.form.on("Omni Content Media", {
	image(frm) {
		render_previews(frm);
	},
	media_items_remove(frm) {
		render_previews(frm);
	},
});

function preview_items(doc) {
	const out = [];
	if (doc.media) out.push({ url: doc.media, label: __("Ảnh bìa") });
	(doc.media_items || []).forEach((row, i) => {
		if (row.image) out.push({ url: row.image, label: __("Ảnh {0}", [i + 2]) });
	});
	return out;
}

function render_previews(frm) {
	frm.$wrapper.find(".omni-media-preview").remove();
	const items = preview_items(frm.doc).filter((x) => /^(\/files\/|\/private\/files\/|\/omni-media\/)/.test(x.url));
	if (!items.length) return;
	const cells = items
		.map((x) => {
			const url = frappe.utils.escape_html(encodeURI(x.url));
			return `<a href="${url}" target="_blank" rel="noopener" style="display:inline-block;margin:0 12px 8px 0;text-align:center;">
				<img src="${url}" alt="" loading="lazy" style="height:96px;max-width:160px;object-fit:contain;border:1px solid var(--border-color);border-radius:6px;background:var(--bg-light-gray);">
				<div class="text-muted small">${frappe.utils.escape_html(x.label)}</div>
			</a>`;
		})
		.join("");
	$(`<div class="omni-media-preview form-section" style="padding:12px 15px 4px;">${cells}</div>`).insertBefore(
		frm.$wrapper.find(".form-layout .form-page").first()
	);
}
