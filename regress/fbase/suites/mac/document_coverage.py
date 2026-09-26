"""Transfer-document test points and their implemented case mappings.

An empty case list is an explicit backlog item, not an omitted document point.
"""


DOCUMENT_TEST_POINTS = {
    "三权分立功能转测.md": {
        point: [] for point in (
            "5.1.1", "5.1.2", "5.1.3", "5.1.4",
            "5.2.1", "5.2.2", "5.2.3", "5.2.4",
            "5.3.1.1", "5.3.1.2", "5.3.2", "5.3.3",
            "5.3.4.1", "5.3.4.2", "5.3.4.3", "5.3.4.4",
            "5.3.4.5", "5.3.4.6", "5.3.4.7", "5.3.4.8",
            "5.3.4.9", "5.3.4.10", "5.3.4.11", "5.3.4.12",
            "5.4.1.1", "5.4.1.2", "5.4.2", "5.4.3", "5.4.4", "5.5",
        )
    },
    "安可强制访问控制功能转测(张娟).md": {
        point: [] for point in (
            "2.2.1", "2.2.2", "2.2.3", "2.2.4", "2.2.5",
            "2.2.6", "2.2.7", "2.2.8", "2.2.9", "2.2.10",
            "2.2.11", "2.2.12", "2.2.13", "2.2.14", "2.2.15",
        )
    },
    "审计功能转测（邹雪、陈群友）.md": {
        point: [] for point in (
            "5.1", "5.2.1", "5.2.2", "5.2.3", "5.3", "5.4",
            "5.4.1", "5.4.2", "5.4.3", "5.4.4",
            "5.5.1", "5.5.2", "5.5.3", "5.5.4", "5.6",
        )
    },
    "安可密码和验证失效需求(陈群友).md": {
        point: [] for point in (
            "一、密码复杂度", "二、密码更换周期", "三、验证失败处理",
            "四、登录成功回显", "五、账户多重验证机制",
            "六、日志密码隐藏", "七、其它测试项",
        )
    },
    "透明加密功能转测（陈群友）.md": {
        point: [] for point in ("1.1", "1.2", "1.3", "1.4")
    },
    "商密和TLCP认证转测.md": {
        point: [] for point in (
            "1.1.1", "1.2", "1.3",
            "2.3.1", "2.3.2", "2.3.3", "2.3.4", "2.3.5",
            "2.3.6", "2.3.7", "2.3.8", "2.3.9", "2.3.10",
            "2.4.1", "2.4.2", "2.5",
        )
    },
    "gb18030.md": {
        point: [] for point in ("一", "二", "三", "4.1", "4.2")
    },
    "故障转移槽转测文档.md": {
        point: [] for point in (
            "2.2", "2.3", "2.4", "3.1", "3.2", "3.3", "3.3.3",
        )
    },
}


# Empty mappings are allowed only when the transfer document contains no
# independently executable scenario or its required external service is absent.
DOCUMENT_TEST_POINT_EXEMPTIONS = {
    "三权分立功能转测.md": {
        "5.1.4": "章节无独立 SQL 步骤，只引用前文限制。",
        "5.2.4": "章节无独立 SQL 步骤，只引用前文限制。",
        "5.4.2": "章节内容为空。",
        "5.4.3": "章节内容为空。",
        "5.4.4": "章节内容为空。",
        "5.5": "章节内容为空。",
    },
    "安可密码和验证失效需求(陈群友).md": {
        "五、账户多重验证机制": (
            "文档未提供 LDAP、Kerberos 或 OS 认证的服务端和 HBA 参数；"
            "当前环境也未部署对应服务。"),
    },
}


def map_case(case_id, document, *points):
    for point in points:
        DOCUMENT_TEST_POINTS[document][point].append(case_id)


map_case("mac.separation_of_duties.sso_system_privileges",
         "三权分立功能转测.md", "5.1.1")
map_case("mac.separation_of_duties.sso_object_creation_restrictions",
         "三权分立功能转测.md", "5.1.2")
map_case("mac.separation_of_duties.sso_role_membership_restrictions",
         "三权分立功能转测.md", "5.1.3")
map_case("mac.separation_of_duties.sao_system_privileges",
         "三权分立功能转测.md", "5.2.1")
map_case("mac.separation_of_duties.sao_object_creation_restrictions",
         "三权分立功能转测.md", "5.2.2")
map_case("mac.separation_of_duties.sao_role_membership_restrictions",
         "三权分立功能转测.md", "5.2.3")
map_case("mac.separation_of_duties.dba_user_management_separation_off",
         "三权分立功能转测.md", "5.3.1.1")
map_case("mac.separation_of_duties.dba_user_management_separation_on",
         "三权分立功能转测.md", "5.3.1.2")
map_case("mac.separation_of_duties.dba_object_privilege_separation",
         "三权分立功能转测.md", "5.3.2")
map_case("mac.separation_of_duties.role_membership_restrictions",
         "三权分立功能转测.md", "5.3.3")
map_case("mac.separation_of_duties.dba_session_switch_restrictions",
         "三权分立功能转测.md", "5.3.4.1")
map_case("mac.separation_of_duties.dba_metadata_access_restrictions",
         "三权分立功能转测.md", "5.3.4.2", "5.3.4.3")
map_case("mac.separation_of_duties.dba_metadata_index_restrictions",
         "三权分立功能转测.md", "5.3.4.4")
map_case("mac.separation_of_duties.dba_metadata_sequence_restrictions",
         "三权分立功能转测.md", "5.3.4.5")
map_case("mac.separation_of_duties.dba_metadata_view_restrictions",
         "三权分立功能转测.md", "5.3.4.6")
map_case("mac.separation_of_duties.dba_metadata_function_restrictions",
         "三权分立功能转测.md", "5.3.4.7")
map_case("mac.separation_of_duties.dba_security_configuration_restrictions",
         "三权分立功能转测.md", "5.3.4.8")
map_case("mac.separation_of_duties.dba_administrator_owner_protection",
         "三权分立功能转测.md", "5.3.4.11")
map_case("mac.separation_of_duties.dba_administrator_owned_protection",
         "三权分立功能转测.md", "5.3.4.12")
map_case("mac.separation_of_duties.createrole_user_management_separation_off",
         "三权分立功能转测.md", "5.4.1.1")
map_case("mac.separation_of_duties.createrole_user_management_separation_on",
         "三权分立功能转测.md", "5.4.1.2")
map_case("mac.separation_of_duties.dba_mac_policy_table_protection",
         "三权分立功能转测.md", "5.3.4.9", "5.3.4.10")
map_case("mac.mac.policy_definition_and_application",
         "安可强制访问控制功能转测(张娟).md",
         "2.2.1", "2.2.2", "2.2.3", "2.2.4", "2.2.5", "2.2.10")
map_case("mac.mac.user_creation_access_and_session_labels",
         "安可强制访问控制功能转测(张娟).md",
         "2.2.6", "2.2.7", "2.2.8")
map_case("mac.mac.table_creation_and_grants",
         "安可强制访问控制功能转测(张娟).md", "2.2.9")
map_case("mac.mac.user_table_operations",
         "安可强制访问控制功能转测(张娟).md", "2.2.11")
map_case("mac.mac.policy_lifecycle",
         "安可强制访问控制功能转测(张娟).md",
         "2.2.12", "2.2.13", "2.2.14", "2.2.15")
map_case("mac.audit.enable_audit",
         "审计功能转测（邹雪、陈群友）.md", "5.1")
map_case("mac.audit.rule_setting_permissions",
         "审计功能转测（邹雪、陈群友）.md", "5.2.1", "5.2.2", "5.2.3")
map_case("mac.audit.rule_cancellation",
         "审计功能转测（邹雪、陈群友）.md", "5.3")
map_case("mac.audit.rule_modification",
         "审计功能转测（邹雪、陈群友）.md", "5.4")
map_case("mac.audit.log_access_restrictions",
         "审计功能转测（邹雪、陈群友）.md", "5.4.1")
map_case("mac.audit.statement_and_object_logs",
         "审计功能转测（邹雪、陈群友）.md", "5.4.3", "5.4.4")
map_case("mac.audit.server_audit_logs",
         "审计功能转测（邹雪、陈群友）.md", "5.4.2")
map_case("mac.audit.log_configuration",
         "审计功能转测（邹雪、陈群友）.md", "5.5.1", "5.5.2", "5.5.3", "5.5.4")
map_case("mac.audit.role_audit_logs",
         "审计功能转测（邹雪、陈群友）.md", "5.6")
map_case("mac.password.complexity_level",
         "安可密码和验证失效需求(陈群友).md", "一、密码复杂度")
map_case("mac.password.change_interval",
         "安可密码和验证失效需求(陈群友).md", "二、密码更换周期")
map_case("mac.password.log_password_masking",
         "安可密码和验证失效需求(陈群友).md", "六、日志密码隐藏")
map_case("mac.password.failed_authentication_lock",
         "安可密码和验证失效需求(陈群友).md", "三、验证失败处理", "四、登录成功回显")
map_case("mac.password.account_rename",
         "安可密码和验证失效需求(陈群友).md", "七、其它测试项")
map_case("mac.gm.license_lifecycle", "商密和TLCP认证转测.md", "1.1.1")
map_case("mac.gm.sm3_authentication", "商密和TLCP认证转测.md", "1.2")
map_case("mac.gm.sm4_tde_lifecycle", "商密和TLCP认证转测.md", "1.3")
map_case("mac.tde.rc4_storage_lifecycle",
         "透明加密功能转测（陈群友）.md", "1.1", "1.3", "1.4")
map_case("mac.tde.dynamic_switch_document_requirement",
         "透明加密功能转测（陈群友）.md", "1.2")
map_case("mac.tlcp.ca_generation_metadata",
         "商密和TLCP认证转测.md", "2.3.1")
map_case("mac.tlcp.server_client_generation",
         "商密和TLCP认证转测.md", "2.3.2", "2.3.3")
map_case("mac.tlcp.export_import",
         "商密和TLCP认证转测.md", "2.3.4", "2.3.10")
map_case("mac.tlcp.udf_audit_logs", "商密和TLCP认证转测.md", "2.5")
map_case("mac.tlcp.tlcp_mutual_auth", "商密和TLCP认证转测.md", "2.4.1")
map_case("mac.tlcp.ssl_mutual_auth", "商密和TLCP认证转测.md", "2.4.2")
map_case("mac.tlcp.certificate_lifecycle",
         "商密和TLCP认证转测.md", "2.3.5", "2.3.6", "2.3.7", "2.3.8", "2.3.9")
map_case("mac.failover_slot.create_view_drop",
         "故障转移槽转测文档.md", "2.2", "2.3", "2.4", "3.1", "3.2")
map_case("mac.failover_slot.standby_replay",
         "故障转移槽转测文档.md", "3.3")
map_case("mac.failover_slot.delayed_commit",
         "故障转移槽转测文档.md", "3.3.3")
map_case("mac.gb18030.database_encoding_and_sql", "gb18030.md", "一", "二", "三")
map_case("mac.gb18030.full_text_search", "gb18030.md", "4.1", "4.2")


def implemented_case_ids():
    return {
        case_id
        for points in DOCUMENT_TEST_POINTS.values()
        for case_ids in points.values()
        for case_id in case_ids
    }


def pending_test_points():
    return [
        (document, point)
        for document, points in DOCUMENT_TEST_POINTS.items()
        for point, case_ids in points.items()
        if not case_ids and point not in DOCUMENT_TEST_POINT_EXEMPTIONS.get(document, {})
    ]


def exempted_test_points():
    return [
        (document, point)
        for document, points in DOCUMENT_TEST_POINT_EXEMPTIONS.items()
        for point in points
    ]
