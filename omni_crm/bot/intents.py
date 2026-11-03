"""Ý định của khách theo bối cảnh nhà thầu (V21-06, F6): một chỗ duy nhất cho danh sách, ánh xạ, tín hiệu điểm, chuyển người.

Thuần, không đụng DB. `analyze`, `scoring`, `guards`, `auto_send`, bộ chấm và patch ánh xạ đều nhập từ đây.
Câu lệnh đang chạy vẫn ghim v1 nên bot còn trả giá trị CŨ (`LEGACY_INTENTS`); mọi nơi dùng phải hiểu cả cũ lẫn mới.
"""

from __future__ import annotations

OPERATION = "Hỏi cách vận hành"
PACKAGE = "Hỏi gói dịch vụ"
FEE = "Hỏi phí"
PROPOSAL = "Yêu cầu đề xuất"
COMPLAINT = "Khiếu nại"
OTHER = "Khác"

# Danh sách ý định mới (schema v3 của analyze_v2, Select `omni_intent`), đúng thứ tự bảng F6.
INTENTS_B2B = (OPERATION, PACKAGE, FEE, PROPOSAL, COMPLAINT, OTHER)
# Giá trị cũ (khách lẻ, câu lệnh v1). Vẫn hợp lệ trong Select và trong kết quả phân tích cũ.
LEGACY_INTENTS = ("Tư vấn", "Hỏi giá", "Đặt hàng", COMPLAINT, OTHER)
ALL_INTENTS = tuple(dict.fromkeys((*LEGACY_INTENTS, *INTENTS_B2B)))

# Ánh xạ giá trị cũ -> mới (F6 mục 2). Khiếu nại và Khác giữ nguyên.
LEGACY_TO_B2B = {"Hỏi giá": FEE, "Đặt hàng": PROPOSAL, "Tư vấn": OPERATION}
# Chiều ngược (chỉ để client giả của bộ đề trả giá trị hợp lệ với schema v1). "Hỏi gói dịch vụ" cũng là "Tư vấn".
B2B_TO_LEGACY = {FEE: "Hỏi giá", PROPOSAL: "Đặt hàng", OPERATION: "Tư vấn", PACKAGE: "Tư vấn"}

# Ý định mới -> tín hiệu điểm (Omni Score Rule.signal). Khác không có tín hiệu.
SIGNAL_OF = {
	OPERATION: "intent_service_info",
	PACKAGE: "intent_service_info",
	FEE: "intent_fee",
	PROPOSAL: "intent_proposal",
	COMPLAINT: "complaint",
	OTHER: None,
}
# Luôn chuyển người, quyết bằng code, độc lập nhóm câu hỏi (Khác: theo nhóm câu hỏi nên không nằm ở đây).
ALWAYS_HANDOFF = frozenset({FEE, PROPOSAL, COMPLAINT})
# Họ "tư vấn dịch vụ": giá trị cũ "Tư vấn" khớp cả hai ý định mới khi chấm bộ đề (không biết khách hỏi vận hành hay gói).
SERVICE_INFO = frozenset({"Tư vấn", OPERATION, PACKAGE})


def normalize(intent: str | None) -> str | None:
	"""Đưa ý định (cũ hoặc mới) về giá trị mới; giá trị lạ/trống trả nguyên (None nếu trống)."""
	if not intent:
		return None
	return LEGACY_TO_B2B.get(intent, intent)


def signal_of(intent: str | None) -> str | None:
	"""Tín hiệu điểm của ý định (hiểu cả giá trị cũ); None nếu không có."""
	return SIGNAL_OF.get(normalize(intent) or "")


def always_handoff(intent: str | None) -> bool:
	"""Ý định này luôn phải chuyển người (hiểu cả giá trị cũ: Hỏi giá, Đặt hàng, Khiếu nại)."""
	return normalize(intent) in ALWAYS_HANDOFF


def same_intent(expected: str | None, actual: str | None) -> bool:
	"""Hai ý định có coi là một khi chấm bộ đề không: so sau ánh xạ cũ->mới; 'Tư vấn' khớp cả hai ý định tư vấn mới."""
	if expected == actual:
		return True
	if expected in SERVICE_INFO and actual in SERVICE_INFO and "Tư vấn" in (expected, actual):
		return True
	return normalize(expected) == normalize(actual)


def adapt_to(intent: str | None, allowed: list | tuple | set) -> str | None:
	"""Chọn biểu diễn của `intent` nằm trong `allowed` (cũ hoặc mới); không có thì trả nguyên."""
	if intent is None or intent in allowed:
		return intent
	for candidate in (normalize(intent), B2B_TO_LEGACY.get(intent)):
		if candidate in allowed:
			return candidate
	return intent
