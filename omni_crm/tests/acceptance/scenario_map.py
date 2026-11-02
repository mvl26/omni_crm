"""Bảng ánh xạ kịch bản nghiệm thu -> tên test (V2-08). Dữ liệu thuần; `test_v2_08_acceptance` kiểm mỗi tên còn tồn tại.

Khóa: mã kịch bản. Giá trị: (mô tả ngắn, [tên test dạng `tệp.Lớp.hàm` trong omni_crm/tests]). Thiếu test thì ghi `None` kèm lý do
(không dùng: mọi dòng hiện có test).
"""

MAP: dict[str, tuple[str, list[str]]] = {
	# --- docs/10 mục 3 (Đợt 1) ---
	"I-01": (
		"3 tin Zalo liền: 1 Lead, 1 lượt, 3 dòng thời gian",
		[
			"test_dot1_integration.TestI01toI02.test_i01_three_messages_one_lead_one_batch_three_timeline_entries"
		],
	),
	"I-02": (
		"Gửi lại 5 lần: 1 bản ghi",
		["test_dot1_integration.TestI01toI02.test_i02_same_event_redelivered_five_times_is_one_record"],
	),
	"I-03": (
		"Form SĐT Zalo rồi email cùng SĐT: 1 Lead",
		[
			"test_dot1_integration.TestI03CrossChannel.test_i03_zalo_form_phone_then_email_signature_same_phone_is_one_lead"
		],
	),
	"I-04": ("Lỗi 2 lần rồi thành công", ["test_worker.TestWorker.test_transient_error_twice_then_success"]),
	"I-05": (
		"Lỗi 3 lần: Manual + cảnh báo",
		["test_dot1_integration.TestI05toI07.test_i05_three_failures_manual_and_admin_alert"],
	),
	"I-06": (
		"Processing kẹt 15 phút: quét -> Retry -> Done",
		["test_dot1_integration.TestI05toI07.test_i06_stuck_processing_15_minutes_goes_retry_then_done"],
	),
	"I-07": (
		"Tắt kênh, 10 tin, bật lại",
		[
			"test_dot1_integration.TestI05toI07.test_i07_channel_off_ten_messages_saved_then_all_processed_after_enable"
		],
	),
	"I-08": ("Sale trả lời Zalo ở sandbox", ["test_outbound.TestSendReply.test_owner_sale_queues_reply_i08"]),
	"I-09": ("Quá 7 ngày: Blocked", ["test_outbound.TestSendReply.test_beyond_seven_days_blocked_i09"]),
	"I-10": ("Webhook sai chữ ký: 401", ["test_webhooks.TestWebhooks.test_bad_signature_401_no_message"]),
	"I-11": (
		"Thư tự động: Ignored",
		["test_email_channel.TestIgnored.test_all_ignored_fixtures_saved_ignored_without_lead"],
	),
	"I-12": (
		"Hai Lead cùng SĐT: Lead gần nhất + 1 nghi trùng",
		["test_resolver.TestResolverBasics.test_two_leads_same_phone_attach_latest_plus_one_candidate"],
	),
	"I-13": ("Gộp hai Lead", ["test_merge.TestMergeLeads.test_all_references_move_and_secondary_is_kept"]),
	# --- docs/13 (Tổng quan, Hộp thư, nhật ký chăm sóc, Chi tiết) ---
	"13-A": (
		"Tổng quan: 6 chỉ số tính đúng tay",
		["test_dashboard.TestMetrics.test_tiles_match_hand_computed_values_for_today"],
	),
	"13-B": (
		"Tổng quan: thứ tự Cần xử lý ngay",
		["test_dashboard.TestAttentionOrder.test_overdue_sorted_by_longest_wait_then_plain_waiting"],
	),
	"13-C": (
		"Tổng quan: Sales User chỉ thấy Lead của mình, trưởng nhóm thấy đội",
		[
			"test_dashboard.TestPermissions.test_sales_user_sees_only_own_leads_in_every_block",
			"test_dashboard.TestPermissions.test_manager_sees_whole_team_and_scope_mine_narrows",
		],
	),
	"13-D": (
		"Tổng quan: công tắc tắt không lỗi, Guest bị từ chối",
		[
			"test_dashboard.TestPermissions.test_dashboard_switch_off_returns_no_blocks",
			"test_dashboard.TestPermissions.test_snapshot_api_is_cached_per_user_and_guest_is_denied",
		],
	),
	"13-E": (
		"Hộp thư: chip Có nháp, nhãn chưa ghi, chip Cuộc gọi",
		[
			"test_inbox_v204.TestInboxV204.test_has_draft_chip_and_unrecorded_label",
			"test_inbox_v204.TestInboxV204.test_channel_chip_for_calls",
		],
	),
	"13-F": (
		"Hộp thư: hồ sơ rút gọn, ẩn SĐT/email người từ chối",
		[
			"test_inbox_v204.TestInboxV204.test_thread_has_mini_profile_with_company_and_tier",
			"test_inbox_v204.TestInboxV204.test_thread_api_hides_phone_and_email_of_person_who_refused_contact",
		],
	),
	"13-G": (
		"Nhật ký chăm sóc: phiên, kết quả, việc tiếp theo",
		[
			"test_care_log.TestSessions.test_three_messages_within_one_hour_make_one_session",
			"test_care_log.TestSaveOutcome.test_next_step_with_due_creates_exactly_one_task_for_lead_owner",
		],
	),
	"13-H": (
		"Nhật ký chăm sóc tính vào phản hồi lần đầu",
		[
			"test_care_log.TestFirstResponseFromCareLog.test_sale_recorded_log_is_first_response_at_contact_time"
		],
	),
	"13-I": (
		"Chi tiết khách: tiêu đề, thanh giai đoạn, quyền",
		[
			"test_customer.TestHeader.test_title_is_company_name_and_tax_id_when_organization_exists",
			"test_customer.TestHeader.test_header_requires_read_permission",
		],
	),
	"13-J": (
		"Chi tiết: người từ chối/đã xóa không còn nút và số",
		[
			"test_customer.TestContacts.test_refused_and_erased_contacts_are_labeled_and_have_no_buttons_or_numbers"
		],
	),
	# --- docs/14 (nhà thầu B2B; docs/10 dòng 10.07) ---
	"14-A": (
		"Định danh theo MST khớp đúng một Organization",
		[
			"test_resolver_orgs.TestTaxId.test_declared_tax_id_attaches_existing_open_lead",
			"test_resolver_orgs.TestTaxId.test_ten_digit_tax_id_matching_two_branches_is_ambiguous",
		],
	),
	"14-B": (
		"Điểm phù hợp khớp Excel",
		[
			"test_prospect_fit.TestFitCases.test_cases_match_excel_expectations",
			"test_prospect_import.TestTemplateExpectation.test_excel_cached_example_row_matches_crm_fit_after_import",
		],
	),
	"14-C": (
		"Importer chạy lại không tạo/sửa gì",
		["test_prospect_import.TestImport.test_reimport_same_file_creates_and_updates_nothing"],
	),
	"14-D": (
		"Chạy thử không ghi gì",
		["test_prospect_import.TestImport.test_dry_run_writes_nothing_and_predicts_the_real_run"],
	),
	"14-E": (
		"Xóa dữ liệu Contact giữ bản ghi ẩn danh",
		[
			"test_erase_contacts.TestEraseContacts.test_erase_contact_clears_personal_data_keeps_anonymous_record"
		],
	),
	"14-F": (
		"Nhập tệp không nới đồng ý 'Từ chối'",
		["test_prospect_import.TestExistingCrmData.test_refused_consent_is_never_loosened_by_import"],
	),
	"14-G": (
		"Không có chức năng gửi hàng loạt",
		[
			"test_v2_02_checks.TestNoBulkSend.test_no_bulk_named_function_and_no_list_recipient_param",
			"test_v2_02_checks.TestNoBulkSend.test_outbound_messages_are_created_only_in_reviewed_files",
		],
	),
	"14-H": (
		"Gộp giữ liên kết Organization/Contact/Care Log",
		[
			"test_merge_orgs.TestLeadMergeKeepsOrganization.test_care_log_link_is_registered_and_present_since_v2_04",
			"test_merge_orgs.TestMergeOrganizations.test_everything_moves_tax_id_goes_to_primary_and_secondary_is_gone",
		],
	),
	# --- docs/15 mục 10 ---
	"15-Meta-1": (
		"Meta: handshake đúng/sai verify token",
		[
			"test_meta_leads.TestHandshake.test_right_token_returns_challenge_raw",
			"test_meta_leads.TestHandshake.test_wrong_token_mode_or_channel_is_403",
		],
	),
	"15-Meta-2": (
		"Meta: chữ ký đúng/sai",
		[
			"test_meta_leads.TestSignatureAndGateway.test_valid_signature_saves_one_event_without_personal_data",
			"test_meta_leads.TestSignatureAndGateway.test_wrong_missing_or_tampered_signature_saves_nothing",
		],
	),
	"15-Meta-3": (
		"Meta: webhook gửi trùng",
		["test_meta_leads.TestSignatureAndGateway.test_duplicate_delivery_is_idempotent"],
	),
	"15-Meta-4": (
		"Meta: Graph lỗi tạm thời thử lại",
		["test_meta_leads.TestRetryAndErrors.test_graph_5xx_is_retried_and_no_token_in_error_or_log"],
	),
	"15-Meta-5": (
		"Meta: đối soát bù lead thiếu",
		["test_meta_leads.TestReconcile.test_missing_leads_are_created_once_and_idempotent_with_webhook"],
	),
	"15-Google-1": (
		"Google: google_key đúng/sai/khác biểu mẫu",
		[
			"test_google_leads.TestWebhook.test_valid_key_saves_and_answers_empty_json_200",
			"test_google_leads.TestWebhook.test_wrong_missing_or_other_forms_key_is_4xx_and_saves_nothing",
		],
	),
	"15-Google-2": (
		"Google: gửi trùng lead_id",
		[
			"test_google_leads.TestWebhook.test_same_lead_id_twice_is_one_event",
			"test_google_leads.TestWorker.test_reposted_lead_after_processing_is_still_one_lead",
		],
	),
	"15-Google-3": (
		"Google: lead thử (is_test)",
		[
			"test_google_leads.TestWorker.test_is_test_lead_is_flagged_not_assigned_and_ignores_real_contact_signals"
		],
	),
	"15-Google-4": (
		"Google: câu hỏi tùy chỉnh ánh xạ đúng",
		["test_google_leads.TestWorker.test_lead_with_custom_columns_campaign_click_id_and_consent"],
	),
	"15-Web-1": (
		"Web form: honeypot",
		["test_web_form.TestEndpoint.test_honeypot_looks_like_success_and_saves_nothing"],
	),
	"15-Web-2": (
		"Web form: quá tần suất",
		["test_web_form.TestEndpoint.test_rate_limit_per_ip_returns_429_then_other_ip_still_ok"],
	),
	"15-Web-3": (
		"Web form: thiếu đồng ý không ghi đồng ý",
		["test_web_form.TestWorker.test_missing_consent_records_nothing_and_sends_nothing"],
	),
	"15-Web-4": (
		"Web form: UTM lưu đúng",
		["test_web_form.TestWorker.test_lead_with_utm_click_landing_page_and_campaign_by_utm"],
	),
	"15-Cross": (
		"Form Meta/web rồi nhắn Zalo cùng SĐT: một Lead, giữ chiến dịch đầu",
		[
			"test_ads_pipeline.TestCrossChannelOneLead.test_web_form_then_zalo_same_phone_is_one_lead_with_both_activities_and_first_touch"
		],
	),
	"15-SLA-1": (
		"SLA: vào 16:50 thứ Sáu -> hạn 17:05",
		["test_ads_pipeline.TestSlaExamples.test_friday_1650_due_1705"],
	),
	"15-SLA-2": (
		"SLA: vào 17:20 thứ Sáu -> hạn 08:05 thứ Bảy",
		["test_ads_pipeline.TestSlaExamples.test_friday_1720_due_saturday_0805"],
	),
	"15-SLA-3": (
		"SLA: vào 11:55 thứ Bảy -> hạn 08:10 thứ Hai",
		["test_ads_pipeline.TestSlaExamples.test_saturday_1155_due_monday_0810"],
	),
	# --- V2-08 (mới): bảo mật và sự cố ---
	"S-01": (
		"Guest không gọi được API nào ngoài webhook",
		["test_security_dot1.TestWebhookExposure.test_only_webhook_allows_guest"],
	),
	"S-02": (
		"Webhook quảng cáo không chữ ký/sai khóa",
		[
			"test_v2_08_acceptance.TestAdWebhooksRejectUnsigned.test_meta_and_google_without_or_with_wrong_credentials_store_nothing"
		],
	),
	"S-03": (
		"Sales User không đọc được Lead của người khác qua API chi tiết",
		["test_v2_02_checks.TestLeadScopedApisDenyOthers.test_other_sales_user_and_guest_are_denied"],
	),
	"S-04": (
		"Race tạo chiến dịch mới giữa hai worker",
		[
			"test_v2_08_fixes.TestCampaignCreateRace.test_second_creator_of_the_same_campaign_reuses_it_instead_of_failing"
		],
	),
}
