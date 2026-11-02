"""Kiểm tra token chống bot (Turnstile/reCAPTCHA) — tùy chọn, tắt mặc định. Timeout 10 giây, KHÔNG thử lại, lỗi = từ chối."""

from __future__ import annotations

import frappe
import requests

ENDPOINTS = {
	"Turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
	"reCAPTCHA": "https://www.google.com/recaptcha/api/siteverify",
}
TIMEOUT = 10


def verify(channel_doc, provider: str, token: str) -> bool:
	"""True nếu nhà cung cấp xác nhận token. Thiếu khóa/token hay lỗi mạng đều coi là không đạt (không lộ khóa trong log)."""
	secret = channel_doc.get_password("webform_captcha_secret", raise_exception=False)
	url = ENDPOINTS.get(provider)
	if not (secret and token and url) or len(token) > 4096:
		return False
	try:
		resp = requests.post(url, data={"secret": secret, "response": token}, timeout=TIMEOUT)
		return bool(resp.json().get("success"))
	except (requests.RequestException, ValueError):
		frappe.logger("omni").warning("omni: kiem tra chong bot loi mang/khong doc duoc")
		return False
