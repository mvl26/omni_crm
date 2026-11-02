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


class TestOmniUtmScript(FrappeTestCase):
	"""V2-05: kiểm tĩnh `omni_utm.js` (CHƯA thử trên trình duyệt)."""

	def test_script_only_uses_session_storage_and_has_no_network_or_cookie(self):
		js = (APP / "public/js/omni_utm.js").read_text(encoding="utf-8")
		code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
		self.assertIn("sessionStorage", code)
		for banned in ("document.cookie", "fetch(", "XMLHttpRequest", "localStorage", "eval(", "innerHTML"):
			self.assertNotIn(banned, code)

	def test_website_guide_exists_and_has_no_secret(self):
		guide = (APP.parent / "docs/huong-dan/gan-form-website.md").read_text(encoding="utf-8")
		self.assertIn("omni_crm.api.webhooks.web_form", guide)
		self.assertNotRegex(guide, r"(?i)(secret|token)\s*[:=]\s*['\"][A-Za-z0-9]{12,}")
