"""V2-02: thêm giai đoạn bán hàng B2B (Lead/Deal Status) và lý do 'Không phù hợp' (CRM Lost Reason). Chạy lại được.

Chỉ THÊM; không đổi trạng thái của bất kỳ Lead/Deal nào, không xóa trạng thái có sẵn (xem prospect/stages.py).
"""

from omni_crm.prospect.stages import ensure_stages


def execute() -> None:
	ensure_stages()
