"""D3-03: interface STT, registry lười, FakeSTTProvider, cổng Q11 và thử lại của omni_crm.stt.client. Không gọi mạng."""

import frappe
from frappe.tests.utils import FrappeTestCase

from omni_crm.stt import client, registry
from omni_crm.stt.base import STTBlockedError, STTError, Transcript, TransientSTTError
from omni_crm.stt.fake import FakeSTTProvider

AUDIO = b"RIFF-gia"


class TestSTT(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.conf = frappe.local.conf
		self.old = self.conf.get(client.ALLOW_DATA_CONF)
		self.conf[client.ALLOW_DATA_CONF] = 0
		self.addCleanup(self._restore)

	def _restore(self):
		if self.old is not None:
			self.conf[client.ALLOW_DATA_CONF] = self.old
		else:
			self.conf.pop(client.ALLOW_DATA_CONF, None)

	def test_fake_tra_transcript(self):
		p = FakeSTTProvider({AUDIO: "xin chào"})
		t = p.transcribe(AUDIO, "audio/wav", stereo=True)
		self.assertIsInstance(t, Transcript)
		self.assertEqual(t.text, "xin chào")
		self.assertEqual(t.segments[0]["text"], "xin chào")
		self.assertEqual(set(t.segments[0]), {"speaker", "start", "end", "text"})
		self.assertEqual(t.confidence, 0.9)
		self.assertEqual(p.transcribe(b"khac", "audio/wav", stereo=False).text, p.default_text)

	def test_registry_lười_va_fake_chi_dev(self):
		self.assertEqual(registry.get_provider("Fake").name, "Fake")
		self.assertIn("Fake", registry.available_names())
		registry.unregister_provider("Fake")
		with self.assertRaises(registry.STTProviderNotFoundError):
			registry.get_provider("KhongCo")
		with self.assertRaises(registry.STTProviderNotFoundError):
			registry.get_provider(None)
		from unittest import mock

		with mock.patch.object(registry, "dev_allowed", return_value=False):
			with self.assertRaises(registry.STTProviderNotFoundError):
				registry.get_provider("Fake")
			self.assertNotIn("Fake", registry.available_names())

	def test_cong_q11(self):
		real = FakeSTTProvider()
		real.is_real = True
		with self.assertRaises(STTBlockedError):
			client.transcribe(real, AUDIO, "audio/wav")
		self.assertEqual(real.calls, [])
		self.assertEqual(client.transcribe(real, AUDIO, "audio/wav", synthetic=True).confidence, 0.9)
		self.conf[client.ALLOW_DATA_CONF] = 1
		self.assertTrue(client.transcribe(real, AUDIO, "audio/wav").text)
		# nhà cung cấp giả không gửi gì ra ngoài nên không chịu cổng
		self.conf[client.ALLOW_DATA_CONF] = 0
		self.assertTrue(client.transcribe(FakeSTTProvider(), AUDIO, "audio/wav").text)

	def test_thu_lai_gioi_han(self):
		sleeps = []
		p = FakeSTTProvider(faults=["transient", "transient"])
		self.assertTrue(client.transcribe(p, AUDIO, "audio/wav", sleep=sleeps.append).text)
		self.assertEqual(len(p.calls), 3)
		self.assertEqual(sleeps, [2, 6])
		p = FakeSTTProvider(faults=["transient"] * 5)
		with self.assertRaises(STTError):
			client.transcribe(p, AUDIO, "audio/wav", sleep=lambda s: None)
		self.assertEqual(len(p.calls), 3)  # 1 + 2 lần thử lại
		p = FakeSTTProvider(faults=["fatal"])
		with self.assertRaises(STTError):
			client.transcribe(p, AUDIO, "audio/wav", sleep=lambda s: None)
		self.assertEqual(len(p.calls), 1)  # lỗi không thể thử lại thì không thử lại

	def test_loi_khong_lo_bi_mat(self):
		class Leaky(FakeSTTProvider):
			def transcribe(self, audio, mime, *, stereo):
				raise TransientSTTError("401 key=" + "k" * 40 + " khách 0912345678")

		with self.assertRaises(STTError) as cm:
			client.transcribe(Leaky(), AUDIO, "audio/wav", sleep=lambda s: None)
		self.assertNotIn("k" * 40, str(cm.exception))
		self.assertNotIn("0912345678", str(cm.exception))
