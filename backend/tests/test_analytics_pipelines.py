"""多维分析实验平台核心链路测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.store import store
from app.services.experiment import ExperimentService
from app.services.sample_group import SampleGroupService
from app.services.ab_result import AbResultService
from app.services.metric_def import MetricDefService
from app.services.expr_factor import ExprFactorService
from app.services.segment_compare import SegmentCompareService
from app.services.attr_analysis import AttrAnalysisService
from app.services.anomaly_detect import AnomalyDetectService
from app.services.forecast_model import ForecastModelService
from app.services.insight_report import InsightReportService
from app.services.metric_alias import MetricAliasService
from app.services.evaluate_board import EvaluateBoardService
from app.services.data_freshness import DataFreshnessService
from app.services.cohort_track import CohortTrackService
from app.services.data_export import DataExportService
from app.services.drill_down import DrillDownService

def _clear_all():
    for k in list(store._tables.keys()):
        store._tables[k] = []

def test_pipeline_experiment_to_sample_groups():
    """实验创建后自动生成对照和实验组分群。"""
    _clear_all()
    es = ExperimentService()
    entry, ms = es.create_entry({
        "方案编号": "EXPE-001",
        "实验名称": "新首页布局",
        "对照组说明": "旧版首页",
        "实验组说明": "新版首页",
        "样本总量": "10000",
        "分流比例": "5:5",
        "启动日期": "2026-11-01",
        "预期时长": "14",
    })
    assert entry is not None, ms
    sg_rows = store.rows("sample_group")
    assert len(sg_rows) >= 2, f"应创建对照组和实验组，实际{len(sg_rows)}"
    control = [r for r in sg_rows if r.get("group_type") == "control"]
    exp = [r for r in sg_rows if r.get("group_type") == "experiment"]
    assert len(control) >= 1
    assert len(exp) >= 1
    assert int(control[0].get("样本基数", 0)) > 0
    assert int(exp[0].get("样本基数", 0)) > 0

def test_sample_group_members():
    """分群能生成带差异的用户列表。"""
    _clear_all()
    # 先创建实验再用自动分群
    es = ExperimentService()
    es.create_entry({"方案编号":"EXPE-002","实验名称":"测试","对照组说明":"控","实验组说明":"试","样本总量":"200","分流比例":"5:5"})
    sg = SampleGroupService()
    members = sg.get_group_members(1)
    assert len(members) > 0, "应有分群用户"
    assert "user_id" in members[0]
    assert "metric_value" in members[0]

def test_metric_def_creation():
    """指标定义创建和口径版本。"""
    _clear_all()
    md = MetricDefService()
    entry, ms = md.create_entry({
        "指标编号": "M-001",
        "指标名称": "点击率",
        "计算公式": "clicks / impressions",
        "统计粒度": "日",
        "时间窗口": "7",
        "基准取值": "0.10",
        "精度位数": "4",
        "口径版本": "v1",
    })
    assert entry is not None, ms

def test_ab_result_analysis():
    """AB分析生成对照和实验组对比。"""
    _clear_all()
    ab = AbResultService()
    results = ab.run_analysis({"样本范围": "4"})
    assert len(results) == 4
    for r in results:
        assert "指标值" in r
        assert "对比基准" in r
        assert "差异" in r

def test_expr_factor_creation():
    """实验因子互斥规则。"""
    _clear_all()
    ef = ExprFactorService()
    entry, ms = ef.create_entry({
        "因子编号": "F-001",
        "因子名称": "按钮颜色",
        "因子层级": "UI层",
        "取值集合": "红色,蓝色",
        "分配比例": "5:5",
        "互斥规则": "颜色因子互斥",
        "关联实验": "EXPE-001",
        "生效批次": "B001",
    })
    assert entry is not None, ms

def test_anomaly_detect():
    """异常检测计算移动平均。"""
    _clear_all()
    ad = AnomalyDetectService()
    ad.create_entry({
        "规则编号": "AD-001",
        "监控指标": "点击率",
        "检测算法": "moving_average",
        "基线窗口": "7",
        "波动阈值": "0.3",
        "告警级别": "高",
        "静默时段": "0",
        "规则版本": "v1",
    })
    result = ad.run_detection(1, [0.10, 0.12, 0.11, 0.13, 0.15, 0.14, 0.55])
    assert result.get("ok") or "anomaly" in str(result).lower()

def test_forecast_model():
    """预测模型简单移动平均。"""
    _clear_all()
    fc = ForecastModelService()
    fc.create_entry({
        "模型编号": "FC-001",
        "预测对象": "点击率",
        "历史窗口": "7",
        "预测周期": "3",
        "模型类型": "moving_average",
        "参数配置": "window=3",
        "训练批次": "T001",
        "评估误差": "",
    })
    result = fc.train_and_predict(1, [0.10, 0.12, 0.11, 0.13, 0.15, 0.14, 0.16])
    assert result.get("ok"), result

def test_metric_alias_version():
    """口径版本注册。"""
    _clear_all()
    ma = MetricAliasService()
    entry, ms = ma.create_entry({
        "版本编号": "ALIAS-001",
        "指标编号": "M-001",
        "变更说明": "修改去重逻辑",
        "旧口径": "clicks/impressions",
        "新口径": "clicks/valid_impressions",
        "生效时间": "2026-11-16",
        "审批状态": "草稿",
        "变更人": "admin",
    })
    assert entry is not None, ms



# ---------------------------------------------------------------------------
# V025 样本分群成员明细：稳定、可核对、空群/超限有明确结果
# ---------------------------------------------------------------------------

def _reset_sample_groups(rows):
    store.rows("sample_group").clear()
    store.rows("sample_group").extend(rows)
    for table in ("sample_group_members", "sample_group_member_snapshots", "sample_group_member_audit"):
        store.aux_rows(table).clear()
    store.bump_version("sample_group")


def test_sample_group_members_are_stable_across_repeated_reads():
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-STABLE", "分群名称": "稳定",
        "筛选条件": "platform='ios' AND age>=18", "样本基数": "400",
        "group_type": "experiment", "experiment_id": 1,
    }])
    sg = SampleGroupService()
    first = sg.get_member_snapshot(1)
    second = sg.get_member_snapshot(1)
    assert first["state"] == "ready"
    assert first["matched_count"] > 0
    assert first["member_count"] == first["matched_count"]
    assert [m["member_key"] for m in first["members"]] == [m["member_key"] for m in second["members"]]
    assert [m["metric_value"] for m in first["members"]] == [m["metric_value"] for m in second["members"]]


def test_sample_group_reads_its_own_filter_condition():
    _reset_sample_groups([
        {"id": 1, "status": "已就绪", "分群编号": "SG-IOS", "分群名称": "iOS",
         "筛选条件": "platform='ios'", "样本基数": "600"},
        {"id": 2, "status": "已就绪", "分群编号": "SG-WEB", "分群名称": "Web",
         "筛选条件": "platform='web'", "样本基数": "600"},
    ])
    sg = SampleGroupService()
    ios = sg.get_member_snapshot(1)
    web = sg.get_member_snapshot(2)
    assert ios["matched_count"] != web["matched_count"] or ios["members"][0]["member_key"] != web["members"][0]["member_key"]
    assert ios["filter_conditions"][0]["field"] == "platform"
    assert ios["matched_count"] < 600


def test_empty_group_reports_zero_members_and_keeps_base():
    _reset_sample_groups([{
        "id": 1, "status": "草稿", "分群编号": "SG-EMPTY", "分群名称": "空群",
        "筛选条件": "country='ZZ'", "样本基数": "1200",
    }])
    sg = SampleGroupService()
    snapshot = sg.get_member_snapshot(1)
    assert snapshot["state"] == "empty"
    assert snapshot["matched_count"] == 0
    assert snapshot["members"] == []
    assert snapshot["sample_base"] == 1200
    assert snapshot["ready"] is False
    entry, message = sg.run_action(1, "确认就绪")
    assert entry is None and "空群" in message


def test_truncated_group_keeps_full_base_and_reports_unexpanded():
    _reset_sample_groups([{
        "id": 1, "status": "计算中", "分群编号": "SG-BIG", "分群名称": "超限",
        "筛选条件": "", "样本基数": "1200",
    }])
    sg = SampleGroupService()
    snapshot = sg.get_member_snapshot(1)
    assert snapshot["state"] == "truncated"
    assert snapshot["sample_base"] == 1200
    assert snapshot["matched_count"] == 1200
    assert snapshot["member_count"] == snapshot["member_limit"] == 500
    assert snapshot["unexpanded_count"] == 700
    assert snapshot["ready"] is True
    entry, _ = sg.run_action(1, "确认就绪")
    assert entry is not None and entry["status"] == "已就绪"


def test_missing_base_and_invalid_filter_are_explained():
    _reset_sample_groups([
        {"id": 1, "status": "草稿", "分群编号": "SG-NOBASE", "分群名称": "无基数",
         "筛选条件": "", "样本基数": "未知"},
        {"id": 2, "status": "草稿", "分群编号": "SG-BAD", "分群名称": "坏条件",
         "筛选条件": "unknown_field=1", "样本基数": "100"},
    ])
    sg = SampleGroupService()
    assert sg.get_member_snapshot(1)["state"] == "no_data"
    assert sg.get_member_snapshot(2)["state"] == "invalid"
    assert "无法解析" in sg.get_member_snapshot(2)["reason"]


def test_failed_computation_keeps_last_complete_members():
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-FAIL", "分群名称": "失败",
        "筛选条件": "", "样本基数": "200",
    }])
    sg = SampleGroupService()
    good = sg.get_member_snapshot(1)
    keys = [m["member_key"] for m in good["members"]]
    original = sg._compute

    def boom(_group, _fingerprint):
        raise RuntimeError("模拟计算异常")

    sg._compute = boom
    failed = sg.get_member_snapshot(1, force=True)
    assert failed["state"] == "failed"
    assert failed["ready"] is False
    assert [m["member_key"] for m in failed["members"]] == keys

    sg._compute = original
    recovered = sg.get_member_snapshot(1, force=True)
    assert recovered["state"] == "ready"
    assert [m["member_key"] for m in recovered["members"]] == keys


def test_residue_rows_trigger_rollback_before_serving():
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-RESIDUE", "分群名称": "残行",
        "筛选条件": "", "样本基数": "100",
    }])
    sg = SampleGroupService()
    snapshot = sg.get_member_snapshot(1)
    store.aux_rows("sample_group_members").append({
        "snapshot_id": "bogus", "group_id": 1, "ordinal": 999, "member_key": "1:STALE",
        "user_id": "STALE", "group_type": "control", "metric_value": 0.5,
    })
    repaired = sg.get_member_snapshot(1)
    assert [m["member_key"] for m in repaired["members"]] == [m["member_key"] for m in snapshot["members"]]
    audit = sg.get_member_audit(1)
    assert audit["residue"]["ok"] is True
    assert any(row["reason_code"] == "residue_rollback" for row in audit["audit"])


def test_empty_member_table_rolls_back_and_recomputes():
    """V025：核对明细表为空但快照仍在时，整体回滚后重算，不返回空数据残留。"""
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-V025", "分群名称": "留档",
        "筛选条件": "", "样本基数": "150",
    }])
    sg = SampleGroupService()
    first = sg.get_member_snapshot(1)
    store.aux_rows("sample_group_members").clear()
    repaired = sg.get_member_snapshot(1)
    assert repaired["state"] == "ready"
    assert repaired["member_count"] == first["member_count"]
    assert [m["member_key"] for m in repaired["members"]] == [m["member_key"] for m in first["members"]]


def test_list_and_detail_share_the_same_confirmed_result():
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-SHARE", "分群名称": "一致",
        "筛选条件": "", "样本基数": "300",
    }])
    sg = SampleGroupService()
    items, _ = sg.list_entries()
    summary = items[0]["member_summary"]
    detail = sg.get_member_snapshot(1)
    assert summary["state"] == detail["state"]
    assert summary["member_count"] == detail["member_count"]
    assert summary["sample_base"] == detail["sample_base"]
    assert summary["snapshot_id"] == detail["snapshot_id"]


def test_metric_def_consumer_reads_member_values_against_base():
    """V025 跨模块联动：指标定义按分群基数校验有效成员数量。"""
    _reset_sample_groups([{
        "id": 1, "status": "已就绪", "分群编号": "SG-CONSUME", "分群名称": "消费",
        "筛选条件": "", "样本基数": "800",
    }])
    sg = SampleGroupService()
    snapshot = sg.get_member_snapshot(1)
    assert snapshot["sample_base"] >= snapshot["matched_count"]
    assert all(0.0 <= member["metric_value"] <= 1.0 for member in snapshot["members"])
    assert snapshot["member_count"] + snapshot["unexpanded_count"] == snapshot["matched_count"]
