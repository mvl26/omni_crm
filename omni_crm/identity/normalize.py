"""Chuẩn hóa số điện thoại, email, tên người gửi."""


def normalize_sender(channel_type: str, raw: str | None) -> str:
	"""Chuẩn hóa mã người gửi để tạo identity_key.

	Cài đặt tạm của D1-03 (strip, email chữ thường); D1-05 thay bằng chuẩn hóa thật (E.164...).
	"""
	value = (raw or "").strip()
	if channel_type == "Email":
		value = value.lower()
	return value
