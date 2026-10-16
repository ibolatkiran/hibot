"""Adversarial + positive tests for hibot.db.safe_sql (no DB, no pyodbc)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hibot.db import safe_sql  # noqa: E402
from hibot.db.catalog import get_catalog  # noqa: E402

CAT = get_catalog()


def v(sql, region="EU", **kw):
    return safe_sql.validate(sql, region=region, catalog=CAT, **kw)


class SafeSqlBase(unittest.TestCase):
    def assertOk(self, sql, **kw):
        r = v(sql, **kw)
        self.assertTrue(r.ok, "expected OK but got %s for: %s" % (r.errors, sql))
        return r

    def assertRejected(self, sql, needle=None, **kw):
        r = v(sql, **kw)
        self.assertFalse(r.ok, "expected REJECT but passed: %s -> %s" % (sql, r.rewritten_sql))
        self.assertIsNone(r.rewritten_sql)
        if needle:
            self.assertTrue(any(needle.lower() in e.lower() for e in r.errors),
                            "error %s does not mention %r" % (r.errors, needle))
        return r


class TestStatementShape(SafeSqlBase):
    def test_simple_select_ok(self):
        r = self.assertOk("SELECT Id, StatusId FROM Operation.WorkOrder WHERE Id = 1 AND TenantId = 5")
        self.assertIn("TOP 200", r.rewritten_sql)

    def test_stacked_queries_semicolon(self):
        self.assertRejected("SELECT 1; DROP TABLE Operation.WorkOrder", "semicolon")

    def test_stacked_queries_no_semicolon(self):
        self.assertRejected("SELECT 1 SELECT 2", "one statement")

    def test_stacked_delete_without_semicolon(self):
        self.assertRejected("SELECT 1 DELETE FROM Operation.WorkOrder", "DELETE")

    def test_single_trailing_semicolon_ok(self):
        self.assertOk("SELECT 1;")

    def test_double_trailing_semicolon(self):
        self.assertRejected("SELECT 1;;")

    def test_leading_semicolon(self):
        self.assertRejected(";SELECT 1")

    def test_empty_and_whitespace(self):
        self.assertRejected("")
        self.assertRejected("   \n ")
        self.assertRejected("-- just a comment")

    def test_must_start_with_select(self):
        self.assertRejected("(SELECT 1)")
        self.assertRejected("EXEC sp_who")
        self.assertRejected("UPDATE Operation.WorkOrder SET StatusId = 1")

    def test_union_two_selects_ok_each_limited(self):
        r = self.assertOk("SELECT Id FROM Catalog.Status UNION ALL SELECT Id FROM Catalog.Priority")
        self.assertEqual(r.rewritten_sql.upper().count("TOP 200"), 2)


class TestForbiddenKeywords(SafeSqlBase):
    def test_dml_ddl(self):
        for kw in ["INSERT INTO Log.DB_Errors(ErrorMessage) VALUES ('x')",
                   "DELETE FROM Log.DB_Errors",
                   "TRUNCATE TABLE Log.DB_Errors",
                   "DROP TABLE Log.DB_Errors",
                   "ALTER TABLE Log.DB_Errors ADD x int",
                   "CREATE TABLE x (a int)",
                   "MERGE Log.DB_Errors AS t USING Log.DB_Errors AS s ON 1=0 WHEN MATCHED THEN DELETE",
                   "GRANT SELECT ON Log.DB_Errors TO public"]:
            with self.subTest(sql=kw):
                self.assertRejected(kw)

    def test_exec_variants(self):
        self.assertRejected("EXEC('SELECT 1')")
        self.assertRejected("EXECUTE sp_executesql N'select 1'")
        self.assertRejected("SELECT 1 EXEC xp_cmdshell 'dir'")
        self.assertRejected("SELECT dbo.sp_helptext(1)")
        self.assertRejected("SELECT * FROM master.dbo.xp_cmdshell")

    def test_select_into(self):
        self.assertRejected("SELECT * INTO #t FROM Catalog.Status")
        self.assertRejected("SELECT Id INTO dbo.leak FROM Catalog.Status", "INTO")

    def test_openrowset_family(self):
        self.assertRejected("SELECT * FROM OPENROWSET('SQLNCLI','x','select 1')")
        self.assertRejected("SELECT * FROM OPENQUERY(lnk, 'select 1')")
        self.assertRejected("SELECT * FROM OPENDATASOURCE('a','b').c.d.e")

    def test_admin_statements(self):
        for s in ["WAITFOR DELAY '0:0:10'", "SHUTDOWN", "DBCC CHECKDB", "BACKUP DATABASE x TO DISK='y'",
                  "RESTORE DATABASE x", "KILL 55", "USE master", "SET NOCOUNT ON",
                  "DECLARE @a int", "BEGIN TRAN", "PRINT 1", "RAISERROR('x',16,1)"]:
            with self.subTest(sql=s):
                self.assertRejected(s)

    def test_set_declare_after_select(self):
        self.assertRejected("SELECT 1 SET NOCOUNT OFF")
        self.assertRejected("SELECT 1 DECLARE @a int")

    def test_cursor(self):
        self.assertRejected("SELECT 1 OPEN c")
        self.assertRejected("SELECT 1 FETCH NEXT FROM c")

    def test_offset_fetch_and_next_value_for(self):
        self.assertRejected("SELECT Id FROM Catalog.Status ORDER BY Id OFFSET 10 ROWS")
        self.assertRejected("SELECT NEXT VALUE FOR dbo.Seq")

    def test_for_update_and_output(self):
        self.assertRejected("SELECT Id FROM Catalog.Status FOR UPDATE")
        self.assertRejected("SELECT Id FROM Catalog.Status OUTPUT inserted.Id")

    def test_option_restrictions(self):
        self.assertOk("SELECT Id FROM Catalog.Status OPTION (RECOMPILE)")
        self.assertRejected("SELECT Id FROM Catalog.Status OPTION (USE HINT('x'))")
        self.assertRejected("SELECT Id FROM Catalog.Status OPTION (MAXDOP 8)")

    def test_lock_hints_rejected(self):
        self.assertRejected("SELECT Id FROM Catalog.Status WITH (UPDLOCK)", "hint")
        self.assertRejected("SELECT Id FROM Catalog.Status s (XLOCK)", "hint")
        self.assertRejected("SELECT Id FROM Catalog.Status WITH (HOLDLOCK)", "hint")

    def test_nolock_hint_allowed(self):
        self.assertOk("SELECT Id FROM Catalog.Status s WITH (NOLOCK)")
        self.assertOk("SELECT Id FROM Catalog.Status WITH (READUNCOMMITTED)")


class TestNoFalseRejects(SafeSqlBase):
    def test_update_in_column_names(self):
        self.assertOk("SELECT [UpdateDate], UpdateDate, UpdateUserId FROM Operation.WorkCase WHERE TenantId = 1")

    def test_bracketed_keyword_alias(self):
        self.assertOk("SELECT Id AS [delete], StatusId AS [Insert] FROM Operation.WorkOrder WHERE TenantId = 1")

    def test_keywords_inside_strings(self):
        r = self.assertOk("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage LIKE '%DROP TABLE; DELETE%'")
        self.assertIn("'%DROP TABLE; DELETE%'", r.rewritten_sql)

    def test_semicolon_and_comment_markers_inside_strings(self):
        self.assertOk("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage = 'a;b -- c /* d */'")

    def test_escaped_quote_in_string(self):
        self.assertOk("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage = 'it''s; DROP TABLE x'")

    def test_unicode_inside_string_ok(self):
        self.assertOk("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage LIKE N'%üğ%'")

    def test_complex_cte_join_window(self):
        sql = """
        WITH last_status AS (
            SELECT g.WorkOrderId, g.StatusId, g.OperationDate,
                   ROW_NUMBER() OVER (PARTITION BY g.WorkOrderId ORDER BY g.OperationDate DESC) AS rn
            FROM Operation.WorkOrderGpsInfo g
            WHERE g.TenantId = 5
        ), cnt AS (
            SELECT WorkOrderId, COUNT(*) AS c FROM Operation.WorkOrderGpsInfo WHERE TenantId = 5 GROUP BY WorkOrderId
        )
        SELECT wo.Id, ls.StatusId, c.c,
               CASE WHEN c.c > 1 THEN 'multi' ELSE 'single' END AS kind,
               DATEDIFF(day, wo.OpenDate, GETUTCDATE()) AS age
        FROM Operation.WorkOrder wo
        JOIN last_status ls ON ls.WorkOrderId = wo.Id AND ls.rn = 1
        LEFT JOIN cnt c ON c.WorkOrderId = wo.Id
        WHERE wo.TenantId = 5 AND wo.Id IN (SELECT w2.Id FROM Operation.WorkOrder w2 WHERE w2.TenantId = 5)
        ORDER BY wo.Id
        """
        r = self.assertOk(sql)
        self.assertEqual(r.rewritten_sql.upper().count("TOP 200"), 1)
        self.assertEqual(r.warnings, [])

    def test_exists_and_not_exists(self):
        self.assertOk("SELECT s.Id FROM Catalog.Status s WHERE EXISTS (SELECT * FROM Catalog.StatusStep x WHERE x.StatusId = s.Id AND x.TenantId = 1)")

    def test_apply_and_outer_apply(self):
        self.assertOk("""SELECT wo.Id, st.FieldText FROM Operation.WorkOrder wo
            OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.Status s JOIN Catalog.AppTranslationEntry e
                         ON e.AppTranslationId = s.AppTranslationId WHERE s.Id = wo.StatusId) st
            WHERE wo.TenantId = 1""")

    def test_comma_join_resolved(self):
        self.assertOk("SELECT s.Id FROM Catalog.Status s, Catalog.Priority p WHERE s.Id = p.Id")

    def test_json_functions(self):
        self.assertOk("SELECT JSON_VALUE(Inserted,'$[0].StatusId') AS s FROM Log.WorkOrderJsonLog WHERE WorkOrderId = 1 AND TenantId = 2")

    def test_openjson_and_string_split(self):
        self.assertOk("SELECT value FROM STRING_SPLIT('a,b', ',')")
        self.assertOk("SELECT j.[key] FROM OPENJSON('{\"a\":1}') j")

    def test_table_valued_function_from_catalog(self):
        r = self.assertOk("SELECT TOP 5 DURUM FROM dbo.GetSiriusWorkOrderHistory(1, 5, 1)")
        self.assertIn("dbo.GetSiriusWorkOrderHistory", r.functions)

    def test_cast_convert_isnull_coalesce(self):
        self.assertOk("SELECT CAST(Id AS nvarchar(20)) a, CONVERT(varchar(10), OpenDate, 120) b, ISNULL(Barcode,'') c, COALESCE(RejectDescription,'') d FROM Operation.WorkOrder WHERE TenantId=1")

    def test_group_by_having(self):
        self.assertOk("SELECT StatusId, COUNT(*) c FROM Operation.WorkOrder WHERE TenantId = 1 GROUP BY StatusId HAVING COUNT(*) > 1")

    def test_trailing_comment_removed(self):
        r = self.assertOk("SELECT 1 -- DROP TABLE x\n")
        self.assertNotIn("DROP", r.rewritten_sql)

    def test_block_comment_removed_and_separates_tokens(self):
        r = self.assertOk("SELECT/**/1/**/AS/**/a")
        self.assertNotIn("/*", r.rewritten_sql)


class TestInjectionTricks(SafeSqlBase):
    def test_comment_splits_keyword(self):
        self.assertRejected("SEL/**/ECT 1")
        self.assertRejected("SELECT 1 /* x */ ; /* y */ DROP TABLE a")

    def test_line_comment_hides_nothing(self):
        self.assertRejected("SELECT 1 --\nDROP TABLE x")

    def test_unterminated_comment_and_string(self):
        self.assertRejected("SELECT 1 /* never closed ; DROP TABLE x", "unterminated")
        self.assertRejected("SELECT 'abc", "unterminated")
        self.assertRejected("SELECT [abc", "unterminated")

    def test_nested_comment_smuggling(self):
        self.assertRejected("SELECT 1 /* a /* b */ ; DROP TABLE x */ ; DROP TABLE y")

    def test_string_break_out_attempt(self):
        self.assertRejected("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage = 'x' OR 1=1; DROP TABLE a --'")

    def test_unicode_fullwidth_semicolon_and_letters(self):
        self.assertRejected("SELECT 1； DROP TABLE x")
        self.assertRejected("ＳＥＬＥＣＴ 1")

    def test_invisible_and_control_characters(self):
        self.assertRejected("SELECT​ 1")
        self.assertRejected("SELECT 1\x00; DROP TABLE x")
        self.assertRejected("SELECT 1 DROP TABLE x")
        self.assertRejected("SELECT\x0b1")

    def test_nonascii_nbsp_between_tokens(self):
        self.assertRejected("SELECT 1")

    def test_string_concatenation_is_just_data(self):
        r = self.assertOk("SELECT 'EXE' + 'C' AS a")
        self.assertIn("'EXE' + 'C'", r.rewritten_sql)

    def test_char_building_is_data_only(self):
        self.assertOk("SELECT CHAR(69)+CHAR(88) AS a")

    def test_odbc_escape_and_param_marker(self):
        self.assertRejected("SELECT {call sp_who}")
        self.assertRejected("SELECT {fn NOW()}")
        self.assertRejected("SELECT ?")
        self.assertRejected("SELECT [a?b] FROM Catalog.Status")

    def test_variables_not_allowed_adhoc(self):
        self.assertRejected("SELECT @a", "variable")
        self.assertRejected("SELECT @@VERSION", "system variables")
        self.assertRejected("SELECT @@SERVERNAME")

    def test_variable_assignment(self):
        self.assertRejected("SELECT @x = 1", allowed_params=["x"])
        self.assertRejected("SELECT 1, @x = Id FROM Catalog.Status", allowed_params=["x"])
        self.assertRejected("SELECT 1 WHERE 1=1 AND @x += 1", allowed_params=["x"])

    def test_temp_tables_and_hash(self):
        self.assertRejected("SELECT * FROM #tmp")
        self.assertRejected("SELECT * FROM ##global")

    def test_dollar_and_backslash_chars(self):
        self.assertRejected("SELECT $IDENTITY")
        self.assertRejected("SELECT 1 \\ 2")
        self.assertRejected("SELECT `a`")

    def test_double_colon(self):
        self.assertRejected("SELECT geography::Point(1,2,4326)")

    def test_number_followed_by_letters(self):
        self.assertRejected("SELECT 1UNION SELECT 2")

    def test_system_functions_not_allowlisted(self):
        for f in ["SELECT SUSER_SNAME()", "SELECT DB_NAME()", "SELECT HOST_NAME()", "SELECT OBJECT_DEFINITION(1)",
                  "SELECT NEWID()", "SELECT APP_NAME()"]:
            with self.subTest(sql=f):
                self.assertRejected(f)

    def test_unknown_scalar_function_unqualified(self):
        self.assertRejected("SELECT GetFoo(1)")

    def test_catalog_procedure_cannot_be_used_as_function(self):
        self.assertRejected("SELECT dbo.getChangesByWorkOrderId(1)", "procedure")

    def test_cross_database_and_linked_server(self):
        self.assertRejected("SELECT * FROM master.dbo.sysdatabases")
        self.assertRejected("SELECT * FROM OtherDb.Operation.WorkOrder", "cross-database")
        self.assertRejected("SELECT * FROM srv.db.dbo.t")

    def test_system_catalog_views_blocked(self):
        self.assertRejected("SELECT name FROM sys.sql_logins")
        self.assertRejected("SELECT * FROM sys.objects")
        self.assertRejected("SELECT * FROM INFORMATION_SCHEMA.TABLES")
        self.assertRejected("SELECT * FROM sys.fn_my_permissions(NULL, 'DATABASE')")

    def test_openjson_with_clause_contents_still_checked(self):
        self.assertRejected("SELECT * FROM OPENJSON('[]') WITH (a int '$.a') x WITH (UPDLOCK)")

    def test_top_percent_and_ties(self):
        self.assertRejected("SELECT TOP 100 PERCENT Id FROM Catalog.Status")
        self.assertRejected("SELECT TOP 5 WITH TIES Id FROM Catalog.Status ORDER BY Id")


class TestRowLimit(SafeSqlBase):
    def test_default_injected(self):
        r = self.assertOk("SELECT Id FROM Catalog.Status")
        self.assertEqual(r.row_limit, 200)
        self.assertTrue(r.rewritten_sql.startswith("SELECT TOP 200"))

    def test_distinct_top_injection_order(self):
        r = self.assertOk("SELECT DISTINCT StatusId FROM Operation.WorkOrder WHERE TenantId=1")
        self.assertTrue(r.rewritten_sql.startswith("SELECT DISTINCT TOP 200"))

    def test_user_top_clamped(self):
        r = self.assertOk("SELECT TOP 50000 Id FROM Catalog.Status")
        self.assertIn("TOP 200", r.rewritten_sql)
        self.assertNotIn("50000", r.rewritten_sql)

    def test_user_smaller_top_kept(self):
        r = self.assertOk("SELECT TOP 5 Id FROM Catalog.Status")
        self.assertIn("TOP 5", r.rewritten_sql)
        self.assertNotIn("TOP 200", r.rewritten_sql)

    def test_top_parenthesised(self):
        r = self.assertOk("SELECT TOP (99999) Id FROM Catalog.Status", row_limit=300)
        self.assertIn("TOP (300)", r.rewritten_sql)

    def test_row_limit_max_clamped(self):
        r = self.assertOk("SELECT Id FROM Catalog.Status", row_limit=100000)
        self.assertEqual(r.row_limit, 1000)
        self.assertIn("TOP 1000", r.rewritten_sql)
        self.assertTrue(r.warnings)

    def test_bad_row_limit(self):
        self.assertRejected("SELECT 1", row_limit=0)
        self.assertRejected("SELECT 1", row_limit="abc")

    def test_top_expression_rejected(self):
        self.assertRejected("SELECT TOP (SELECT COUNT(*) FROM Catalog.Status) Id FROM Catalog.Status")

    def test_top_param_clamped(self):
        r = self.assertOk("SELECT TOP (@n) Id FROM Catalog.Status", allowed_params=["n"])
        self.assertIn("CASE WHEN @n", r.rewritten_sql)
        self.assertEqual(r.param_order, ["n", "n", "n"])
        self.assertNotIn("@", r.positional_sql)

    def test_subquery_and_cte_not_limited(self):
        r = self.assertOk("WITH a AS (SELECT Id FROM Catalog.Status) SELECT Id FROM a WHERE Id IN (SELECT Id FROM Catalog.Priority)")
        self.assertEqual(r.rewritten_sql.upper().count("TOP"), 1)


class TestCatalogObjects(SafeSqlBase):
    def test_unknown_table(self):
        self.assertRejected("SELECT * FROM Operation.NoSuchTable", "unknown object")

    def test_unqualified_resolves_to_dbo(self):
        self.assertRejected("SELECT * FROM WorkOrder", "unknown object")

    def test_region_specific_object(self):
        self.assertOk("SELECT TOP 5 Id FROM dbo.StatusUpdaterLog WHERE WorkOrderId = 1", region="EU")
        self.assertRejected("SELECT TOP 5 Id FROM dbo.StatusUpdaterLog WHERE WorkOrderId = 1", "region APAC", region="APAC")

    def test_invalid_region(self):
        self.assertRejected("SELECT 1", region="US")

    def test_unknown_column_on_alias(self):
        self.assertRejected("SELECT wo.NoSuchCol FROM Operation.WorkOrder wo WHERE wo.TenantId = 1", "column")

    def test_eu_only_column_in_apac(self):
        self.assertOk("SELECT m.IsWHR FROM Catalog.Material m WHERE m.TenantId = 1", region="EU")
        self.assertRejected("SELECT m.IsWHR FROM Catalog.Material m WHERE m.TenantId = 1", region="APAC")

    def test_procedure_and_unknown_function(self):
        self.assertRejected("SELECT * FROM dbo.getChangesByWorkOrderId", "procedure")
        self.assertRejected("SELECT * FROM dbo.NoSuchFn(1)", "unknown object")

    def test_synonym_rejected(self):
        self.assertRejected("SELECT * FROM dbo.Customer")

    def test_case_insensitive_names(self):
        self.assertOk("select id from operation.workorder where tenantid = 1")

    def test_objects_reported(self):
        r = self.assertOk("SELECT s.Id FROM Catalog.Status s JOIN Catalog.Priority p ON p.Id = s.Id")
        self.assertEqual(sorted(r.objects), ["Catalog.Priority", "Catalog.Status"])


class TestSensitiveColumns(SafeSqlBase):
    def test_password_column_rejected(self):
        self.assertRejected("SELECT Password FROM Catalog.ErpParkDocumentHeaderParams", "credential")
        self.assertRejected("SELECT [PWD] FROM EG.T_WEB_USERS")
        self.assertRejected("SELECT FcmToken FROM V2.DeviceRegistration")

    def test_password_table_rejected(self):
        self.assertRejected("SELECT 1 FROM Catalog.FirstEmployeePassword", "credential")

    def test_aliasing_does_not_hide_secret(self):
        self.assertRejected("SELECT Password AS x FROM Catalog.ErpParkDocumentHeaderParams")

    def test_pii_column_rejected_adhoc(self):
        self.assertRejected("SELECT ContactPhone FROM Operation.WorkOrder WHERE TenantId = 1", "personal data")
        self.assertRejected("SELECT Id FROM Operation.WorkOrder WHERE ContactName = 'x' AND TenantId = 1")

    def test_pii_column_allowed_for_templates(self):
        r = self.assertOk("SELECT ContactPhone FROM Operation.WorkOrder WHERE TenantId = 1", allow_pii=True)
        self.assertIn("ContactPhone", r.pii_columns_used)

    def test_secret_still_rejected_for_templates(self):
        self.assertRejected("SELECT Password FROM Catalog.ErpParkDocumentHeaderParams", allow_pii=True)

    def test_star_rewritten_pii_table(self):
        r = self.assertOk("SELECT * FROM Operation.WorkOrder WHERE Id = 1 AND TenantId = 2")
        self.assertNotIn("ContactPhone", r.rewritten_sql)
        self.assertNotIn("ContactName", r.rewritten_sql)
        self.assertIn("[Id]", r.rewritten_sql)
        self.assertTrue(r.rewrites)
        self.assertNotRegex(r.rewritten_sql, r"SELECT TOP 200\s+\*")

    def test_star_rewritten_secret_table(self):
        r = self.assertOk("SELECT * FROM Catalog.ErpParkDocumentHeaderParams")
        self.assertNotIn("Password", r.rewritten_sql)

    def test_qualified_star_rewritten(self):
        r = self.assertOk("SELECT wo.*, s.Id FROM Operation.WorkOrder wo JOIN Catalog.Status s ON s.Id = wo.StatusId WHERE wo.TenantId = 1")
        self.assertNotIn("ContactName", r.rewritten_sql)
        self.assertIn("s.Id", r.rewritten_sql)

    def test_star_join_rewritten_for_all_tables(self):
        r = self.assertOk("SELECT * FROM Operation.WorkOrder wo JOIN Catalog.Status s ON s.Id = wo.StatusId WHERE wo.TenantId = 1")
        self.assertNotIn("ContactPhone", r.rewritten_sql)
        self.assertIn("[s].[IsOpen]", r.rewritten_sql)

    def test_star_on_clean_table_left_alone(self):
        r = self.assertOk("SELECT * FROM Catalog.Priority")
        self.assertIn("*", r.rewritten_sql)

    def test_count_star_untouched(self):
        r = self.assertOk("SELECT COUNT(*) FROM Operation.WorkOrder WHERE TenantId = 1")
        self.assertIn("COUNT(*)", r.rewritten_sql)

    def test_multiplication_not_star(self):
        r = self.assertOk("SELECT Quantity * Price AS t FROM Operation.WorkOrderDetail WHERE TenantId = 1")
        self.assertEqual(r.rewrites, ["injected TOP 200"])

    def test_star_over_view_rejected(self):
        views = [o.name for o in CAT.objects() if o.type == "view" and "EU" in o.regions]
        self.assertTrue(views)
        self.assertRejected("SELECT * FROM %s" % views[0], "view")

    def test_star_over_subquery_with_pii_table_is_safe(self):
        r = self.assertOk("SELECT * FROM (SELECT * FROM Operation.WorkOrder WHERE TenantId = 1) x")
        self.assertNotIn("ContactPhone", r.rewritten_sql)

    def test_star_in_cte_safe(self):
        r = self.assertOk("WITH a AS (SELECT * FROM Operation.WorkOrder WHERE TenantId = 1) SELECT * FROM a")
        self.assertNotIn("ContactPhone", r.rewritten_sql)

    def test_star_over_function_warns(self):
        r = self.assertOk("SELECT * FROM dbo.GetSiriusWorkOrderHistory(1, 5, 1)")
        self.assertTrue(any("function" in w for w in r.warnings))

    def test_exists_star_not_expanded(self):
        r = self.assertOk("SELECT s.Id FROM Catalog.Status s WHERE EXISTS (SELECT * FROM Operation.WorkOrder w WHERE w.StatusId = s.Id AND w.TenantId = 1)")
        self.assertNotIn("[w].[Id]", r.rewritten_sql)


class TestWarnings(SafeSqlBase):
    def test_tenant_warning(self):
        r = self.assertOk("SELECT Id FROM Operation.WorkOrder WHERE StatusId = 3")
        self.assertTrue(any("TenantId" in w for w in r.warnings))

    def test_no_warning_with_predicate(self):
        r = self.assertOk("SELECT Id FROM Operation.WorkOrder WHERE StatusId = 3 AND TenantId = 5")
        self.assertEqual(r.warnings, [])

    def test_no_warning_pk_lookup(self):
        r = self.assertOk("SELECT Id FROM Operation.WorkOrder wo WHERE wo.Id = 77")
        self.assertEqual(r.warnings, [])

    def test_warning_per_table(self):
        r = self.assertOk("SELECT wo.Id FROM Operation.WorkOrder wo JOIN Log.WorkOrderJsonLog l ON l.WorkOrderId = wo.Id WHERE wo.TenantId = 5")
        self.assertTrue(any("WorkOrderJsonLog" in w for w in r.warnings))
        self.assertFalse(any("Operation.WorkOrder (" in w for w in r.warnings))


class TestParams(SafeSqlBase):
    def test_positional_conversion(self):
        r = self.assertOk("SELECT Id FROM Operation.WorkOrder WHERE Id = @WorkOrderId AND TenantId = @TenantId",
                          allowed_params=["WorkOrderId", "TenantId"])
        self.assertEqual(r.param_order, ["WorkOrderId", "TenantId"])
        self.assertEqual(r.positional_sql.count("?"), 2)
        self.assertIn("@WorkOrderId", r.rewritten_sql)

    def test_repeated_param(self):
        r = self.assertOk("SELECT Id FROM Operation.WorkOrder WHERE (@a IS NULL OR Id = @a) AND TenantId = 1",
                          allowed_params=["a"])
        self.assertEqual(r.param_order, ["a", "a"])

    def test_undeclared_param(self):
        self.assertRejected("SELECT Id FROM Operation.WorkOrder WHERE Id = @evil", "variable", allowed_params=["a"])

    def test_question_mark_in_string_ok(self):
        r = self.assertOk("SELECT ErrorID FROM Log.DB_Errors WHERE ErrorMessage LIKE '%?%' AND ErrorID = @a", allowed_params=["a"])
        self.assertEqual(r.param_order, ["a"])


class TestDerivedColumns(SafeSqlBase):
    def test_cte_unknown_column_rejected(self):
        self.assertRejected("WITH a AS (SELECT Id AS X FROM Catalog.Status) SELECT a.Y FROM a", "not produced")

    def test_cte_known_column_ok(self):
        self.assertOk("WITH a AS (SELECT Id AS X FROM Catalog.Status) SELECT a.X FROM a")

    def test_derived_unknown_column_rejected(self):
        self.assertRejected("SELECT d.Q FROM (SELECT Id FROM Catalog.Status) d", "not produced")


class TestIdempotence(SafeSqlBase):
    def test_rewritten_sql_revalidates_and_is_stable(self):
        for sql in ["SELECT * FROM Operation.WorkOrder WHERE TenantId = 1",
                    "SELECT TOP 9999 Id FROM Catalog.Status",
                    "SELECT Id FROM Catalog.Status UNION ALL SELECT Id FROM Catalog.Priority"]:
            with self.subTest(sql=sql):
                r1 = self.assertOk(sql)
                r2 = self.assertOk(r1.rewritten_sql)
                self.assertEqual(r1.rewritten_sql, r2.rewritten_sql)

    def test_output_has_no_comments_or_semicolons(self):
        r = self.assertOk("SELECT /* x */ Id -- y" + chr(10) + " FROM Catalog.Status; ")
        for bad in ("--", "/*", "*/", ";"):
            self.assertNotIn(bad, r.rewritten_sql)


class TestLimits(SafeSqlBase):
    def test_too_long(self):
        self.assertRejected("SELECT " + "1," * 20000 + "1")

    def test_deep_nesting(self):
        sql = "SELECT " + "(" * 30 + "1" + ")" * 30
        self.assertRejected(sql)

    def test_unbalanced(self):
        self.assertRejected("SELECT (1")
        self.assertRejected("SELECT 1)")

    def test_result_dict(self):
        d = v("SELECT 1").to_dict()
        self.assertTrue(d["ok"])


if __name__ == "__main__":
    unittest.main()
