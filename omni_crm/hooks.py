app_name = "omni_crm"
app_title = "Omni CRM"
app_publisher = "Miyano Việt Nam"
app_description = "Tích hợp đa kênh email, Zalo OA, tổng đài với Frappe CRM, kèm bot AI"
app_email = "anh.dn@miyano.com.vn"
app_license = "agpl-3.0"

# Apps
# ------------------

required_apps = ["crm"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "omni_crm",
# 		"logo": "/assets/omni_crm/logo.png",
# 		"title": "Omni CRM",
# 		"route": "/omni_crm",
# 		"has_permission": "omni_crm.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/omni_crm/css/omni_crm.css"
# app_include_js = "/assets/omni_crm/js/omni_crm.js"

# include js, css files in header of web template
# web_include_css = "/assets/omni_crm/css/omni_crm.css"
# web_include_js = "/assets/omni_crm/js/omni_crm.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "omni_crm/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "omni_crm/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "omni_crm.utils.jinja_methods",
# 	"filters": "omni_crm.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "omni_crm.install.before_install"
# after_install = "omni_crm.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "omni_crm.uninstall.before_uninstall"
# after_uninstall = "omni_crm.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "omni_crm.utils.before_app_install"
# after_app_install = "omni_crm.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "omni_crm.utils.before_app_uninstall"
# after_app_uninstall = "omni_crm.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "omni_crm.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Khung theo docs/01 mục 6. Chưa bật; các prompt sau bỏ chú thích từng mục.

# doc_events = {
# 	"Communication": {"after_insert": "omni_crm.channels.email.adapter.on_communication_insert"},  # D1-07
# 	"CRM Lead": {
# 		"validate": "omni_crm.crm_bridge.lead.compute_normalized_fields",  # D1-05
# 		"on_update": "omni_crm.crm_bridge.lead.track_manual_edits",  # D1-06
# 	},
# }

# Scheduled Tasks
# ---------------
# Khung theo docs/01 mục 5-6. Chưa bật.

# scheduler_events = {
# 	"cron": {
# 		"*/5 * * * *": [
# 			"omni_crm.pipeline.sweeper.requeue_stuck",
# 			"omni_crm.monitoring.alerts.run_checks",
# 		],
# 		"*/30 * * * *": ["omni_crm.channels.zalo.token.refresh_if_needed"],
# 		"30 1 * * *": ["omni_crm.channels.telephony.registry.reconcile_yesterday"],
# 		"0 2 * * *": ["omni_crm.pipeline.sweeper.cleanup_raw_payloads"],
# 	},
# 	"hourly": ["omni_crm.bot.ai_client.report_budget"],
# }

# Fixtures
# --------
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["Omni Admin", "Omni Knowledge Editor"]]]},
	# Bật từ D1-02:
	# {"dt": "Custom Field", "filters": [["module", "=", "Omni CRM"]]},
	# {"dt": "Property Setter", "filters": [["module", "=", "Omni CRM"]]},
]

# Testing
# -------

# before_tests = "omni_crm.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "omni_crm.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "omni_crm.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["omni_crm.utils.before_request"]
# after_request = ["omni_crm.utils.after_request"]

# Job Events
# ----------
# before_job = ["omni_crm.utils.before_job"]
# after_job = ["omni_crm.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"omni_crm.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
