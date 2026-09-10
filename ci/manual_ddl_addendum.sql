-- manual_ddl_addendum.sql —— dev 库手工 DDL 的权威快照（api-db-bootstrap 附加步骤）。
-- 来源：2026-09-10 从 dev 库 pg_get_functiondef/pg_get_triggerdef 导出。
-- 背景：审批留痕守卫触发器（BR-01 append-only）按 WF-002/WF-006 规格以手工 DDL
-- 落在 dev 库，未入 Django 迁移——全新环境（api-ci）靠本文件补齐同款 DDL。
-- 规格差异登记：dev 版 trg_aae_guard 未含 pg_trigger_depth 级联放行口与
-- rp_archiver 归档角色分支（WF-006 §4.2 完整版），如后续收口须双端同步。

-- approval_audit_events_guard on approval_audit_events
CREATE OR REPLACE FUNCTION public.trg_aae_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'approval_audit_events is append-only';
END; $function$;

CREATE TRIGGER approval_audit_events_guard BEFORE DELETE OR UPDATE ON public.approval_audit_events FOR EACH ROW EXECUTE FUNCTION trg_aae_guard();
-- approval_records_guard on approval_records
CREATE OR REPLACE FUNCTION public.trg_approval_records_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF pg_trigger_depth() > 0 THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'approval_records is append-only; direct DELETE rejected';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.action <> 'pending' OR NEW.action NOT IN ('approve','reject','skipped')
       OR NEW.level <> OLD.level OR NEW.approver_id <> OLD.approver_id
       OR NEW.instance_id <> OLD.instance_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'illegal mutation on approval_records';
    END IF;
  END IF;
  RETURN NEW;
END; $function$;

CREATE TRIGGER approval_records_guard BEFORE DELETE OR UPDATE ON public.approval_records FOR EACH ROW EXECUTE FUNCTION trg_approval_records_guard();
