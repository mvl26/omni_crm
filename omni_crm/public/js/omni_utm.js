/*
 * omni_utm.js - lưu UTM / click id của LẦN TRUY CẬP ĐẦU vào sessionStorage để không mất khi khách chuyển trang,
 * rồi gắn vào form gửi về Omni CRM (docs/15 mục 4). Không cookie, không gửi dữ liệu đi đâu cho tới khi khách bấm gửi form.
 * Nhúng: <script src="https://<crm>/assets/omni_crm/js/omni_utm.js" defer></script>
 * Dùng:  OmniUtm.attach(formElement)   // thêm các ô ẩn utm_*, gclid, fbclid, landing_page, referrer, submission_id
 *        OmniUtm.payload()             // hoặc lấy object để tự gửi JSON
 * CHƯA THỬ TRÊN TRÌNH DUYỆT/TRANG THẬT.
 */
(function (global) {
	"use strict";
	var KEY = "omni_utm_v1";
	var PARAMS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid"];

	function read() {
		try {
			return JSON.parse(global.sessionStorage.getItem(KEY) || "null");
		} catch (e) {
			return null;
		}
	}

	function write(data) {
		try {
			global.sessionStorage.setItem(KEY, JSON.stringify(data));
		} catch (e) {
			/* trình duyệt chặn sessionStorage: bỏ qua, form vẫn gửi được (mất UTM) */
		}
	}

	function uuid() {
		if (global.crypto && global.crypto.randomUUID) return global.crypto.randomUUID();
		return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
	}

	function capture() {
		if (read()) return; // chỉ lần truy cập ĐẦU
		var q = new global.URLSearchParams(global.location.search);
		var data = { landing_page: global.location.href.split("#")[0].slice(0, 500), referrer: (global.document.referrer || "").slice(0, 500) };
		PARAMS.forEach(function (k) {
			var v = q.get(k);
			if (v) data[k] = v.slice(0, 200);
		});
		write(data);
	}

	function payload() {
		var data = read() || {};
		var out = {};
		Object.keys(data).forEach(function (k) { out[k] = data[k]; });
		out.submission_id = uuid(); // mỗi lần gửi một mã: gửi lại cùng form không tạo trùng khi dùng lại mã
		return out;
	}

	function attach(form) {
		var data = payload();
		Object.keys(data).forEach(function (k) {
			var input = form.querySelector('input[name="' + k + '"]');
			if (!input) {
				input = global.document.createElement("input");
				input.type = "hidden";
				input.name = k;
				form.appendChild(input);
			}
			input.value = data[k];
		});
	}

	capture();
	global.OmniUtm = { capture: capture, payload: payload, attach: attach };
})(window);
