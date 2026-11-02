"""V2-04: kiểm tĩnh phần giao diện (KHÔNG thay cho thử trên trình duyệt): CSS không lan sang trang khác, trang đã đăng ký."""

import json
import re
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

APP = Path(__file__).resolve().parents[1]


class TestUiStatic(FrappeTestCase):
	def test_css_is_scoped_under_omni_ui_and_never_touches_root(self):
		css = (APP / "public/css/omni.css").read_text(encoding="utf-8")
		css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		self.assertNotIn(":root", css)
		self.assertNotRegex(css, r"(^|\})\s*(body|html|\*)\s*[{,]")
		for block in re.finditer(r"([^{}@]+)\{", css):
			for selector in block.group(1).split(","):
				selector = selector.strip()
				if selector and not re.match(r"^(\d+%|from|to|media\b.*)$", selector):
					self.assertTrue(selector.startswith(".omni-ui"), selector)

	def test_css_is_included_and_pages_have_script_and_roles(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertRegex(hooks, r'(?ms)^app_include_css = \(?\s*"/assets/omni_crm/css/omni\.css"')
		for page in ("omni_dashboard", "omni_customer"):
			base = APP / "omni_crm/page" / page
			meta = json.loads((base / f"{page}.json").read_text(encoding="utf-8"))
			roles = {r["role"] for r in meta["roles"]}
			self.assertTrue({"Sales User", "Omni Admin"} <= roles, page)
			self.assertTrue((base / f"{page}.js").exists())
			self.assertNotIn("Guest", roles)
