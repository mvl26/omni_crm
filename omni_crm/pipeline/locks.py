"""Khóa theo định danh khách (Redis)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import frappe
from redis.exceptions import LockError


class LockBusy(Exception):
	"""Không lấy được khóa định danh trong thời gian chờ: có lượt xử lý khác đang chạy."""


def lock_key(identity_key: str) -> bytes:
	"""Tên khóa Redis, có tiền tố db của site (v15: frappe.cache() là client Redis có make_key)."""
	return frappe.cache().make_key(f"omni:lock:{identity_key}")


@contextmanager
def identity_lock(identity_key: str, timeout: int = 120, wait: int = 30) -> Iterator[None]:
	"""Giữ khóa Redis của định danh tối đa `timeout` giây; chờ `wait` giây, không được thì ném LockBusy."""
	lock = frappe.cache().lock(lock_key(identity_key), timeout=timeout, blocking_timeout=wait)
	if not lock.acquire():
		raise LockBusy(identity_key)
	try:
		yield
	finally:
		try:
			lock.release()
		except LockError:
			pass  # khóa đã hết hạn (timeout) trong lúc xử lý: không làm hỏng luồng chính
