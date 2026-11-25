"""Giá Claude API (USD / 1 triệu token) để ghi chi phí vào Omni Bot Run. Giá công bố, cập nhật 2026-11-03.

Ghi tạo cache tính 1,25 lần giá đầu vào (cache 5 phút). Mô hình không có trong bảng: chi phí 0 và ghi chú trong
docs/17 (cần bổ sung bảng khi đổi mô hình).
"""

from __future__ import annotations

# mô hình: (đầu vào, đầu ra, đọc cache)
PRICES_USD_PER_MTOK: dict[str, tuple[float, float, float]] = {
	"claude-fable-5-2": (8.0, 40.0, 0.20),
	"claude-opus-5-5": (4.0, 20.0, 0.20),
	"claude-opus-5": (5.0, 25.0, 0.50),
	"claude-sonnet-5-5": (2.0, 10.0, 0.10),
	"claude-sonnet-5": (2.0, 10.0, 0.20),
	"claude-haiku-5-5": (0.10, 0.50, 0.01),
	"claude-haiku-4-5": (1.0, 5.0, 0.10),
}
CACHE_WRITE_FACTOR = 1.25


def price_of(model: str | None) -> tuple[float, float, float] | None:
	"""Bộ giá của mô hình (khớp tên đầy đủ); None nếu chưa có trong bảng."""
	return PRICES_USD_PER_MTOK.get((model or "").strip())


def cost_usd(
	model: str | None,
	input_tokens: int = 0,
	output_tokens: int = 0,
	cache_read_tokens: int = 0,
	cache_creation_tokens: int = 0,
) -> float:
	"""Chi phí USD của một lần gọi theo số token; mô hình lạ trả 0."""
	price = price_of(model)
	if not price:
		return 0.0
	p_in, p_out, p_cache = price
	total = (
		(input_tokens or 0) * p_in
		+ (output_tokens or 0) * p_out
		+ (cache_read_tokens or 0) * p_cache
		+ (cache_creation_tokens or 0) * p_in * CACHE_WRITE_FACTOR
	)
	return round(total / 1_000_000, 6)


def estimate_usd(model: str | None, tokens: int) -> float:
	"""Ước lượng trần chi phí cho `tokens` token chưa biết vào/ra: tính theo giá đầu ra (cao nhất)."""
	price = price_of(model)
	return round((tokens or 0) * (price[1] if price else 0) / 1_000_000, 6)
