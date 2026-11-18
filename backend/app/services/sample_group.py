"""样本分群业务规则：提供可核对、可复现的分群成员明细供 AB 分析消费。

V025 修复要点：
- 读取分群自身的「筛选条件」，不再忽略筛选条件直接按样本基数造数。
- 成员明细按分群身份做确定性生成：同一分群连续打开、刷新页面、重复调用，
  成员键与指标值都保持一致。
- 成员上限与分群基数同时写进确认结果：超限时返回截断结果并保留完整基数，
  同时给出未展开数量，不再静默截断。
- 空群（命中 0 人）、暂无数据（无有效基数）、筛选条件异常、计算失败都有
  独立状态与原因，空群不会以「已就绪」的成员结果对外呈现。
- 明细先在局部变量里算完整、通过核对后才整体提交；计算异常时保留最近一次
  完整成员集合，核对表为空或存在残行时整体回滚，不会留下半截明细。
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Iterable

from app.store import store

MODULE = "sample_group"
LIST_FIELDS = ["分群编号", "分群名称", "筛选条件", "样本基数", "分群日期", "更新方式", "有效天数", "负责人"]
REQUIRED_FIELDS = ["分群编号", "分群名称", "筛选条件"]
STATUS_ORDER = ["草稿", "计算中", "已就绪", "已过期"]
ACTION_RULES = {"开始计算": "计算中", "确认就绪": "已就绪", "标记过期": "已过期"}
NEGATIVE_ACTIONS = []

VERSION_TAG = "V025"
# 成员明细展示上限：必须与样本基数一起记录，页面才能解释「成员数」与「样本总量」的差异。
MEMBER_LIMIT = 500
# 带筛选条件时允许逐条核对的最大候选量，超过则明确失败而不是悄悄截断。
SCAN_LIMIT = 20_000

SNAPSHOT_TABLE = "sample_group_member_snapshots"
MEMBER_TABLE = "sample_group_members"
AUDIT_TABLE = "sample_group_member_audit"

STATE_LABELS = {
    "ready": "已确认",
    "truncated": "已截断",
    "empty": "空群",
    "no_data": "暂无数据",
    "invalid": "筛选条件异常",
    "failed": "计算失败",
}
READY_STATES = ("ready", "truncated")
# 无可用成员结果的状态不能确认就绪：空群不能冒充已就绪分群，
# 筛选异常/计算失败需要先修好或重试成功。
BLOCKING_STATES = ("empty", "invalid", "failed")

_LOCK = threading.RLock()
_SEQ = 0


class _ComputeError(Exception):
    """计算阶段的可解释失败，用于回滚并保留最近完整成员集合。"""

    def __init__(self, code: str, reason: str, *, retryable: bool = True) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.retryable = retryable


# ---------------------------------------------------------------------------
# 筛选条件解析
# ---------------------------------------------------------------------------

_CMP_RE = re.compile(r"^\s*([^\s=<>!]+)\s*(>=|<=|==|!=|=|>|<)\s*(.*?)\s*$")
_IN_RE = re.compile(r"^\s*([^\s=<>!]+)\s+(not\s+in|in)\s*[\(\[](.*?)[\)\]]\s*$", re.IGNORECASE)
_AND_RE = re.compile(r"\s+(?:AND|and|And|&&)\s+")

_FIELD_ALIASES = {
    "user_id": "user_id",
    "用户id": "user_id",
    "用户编号": "user_id",
    "experiment_id": "experiment_id",
    "实验id": "experiment_id",
    "实验编号": "experiment_id",
    "group": "group",
    "group_type": "group",
    "分组": "group",
    "实验分组": "group",
    "platform": "platform",
    "平台": "platform",
    "country": "country",
    "国家": "country",
    "city": "city",
    "城市": "city",
    "device": "device",
    "设备": "device",
    "age": "age",
    "年龄": "age",
    "register_days": "register_days",
    "注册天数": "register_days",
    "active_days": "active_days",
    "活跃天数": "active_days",
}
_NUMERIC_FIELDS = {"age", "register_days", "active_days"}
_VALUE_ALIASES = {
    "group": {"对照组": "control", "实验组": "experiment"},
}

_PLATFORMS = ("ios", "android", "web")
_COUNTRIES = ("CN", "US", "JP")
_CITIES = ("上海", "北京", "深圳", "杭州")
_DEVICES = ("phone", "tablet", "desktop")


def _clean_value(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1].strip()
    return token


def _canonical_field(source: str) -> str | None:
    return _FIELD_ALIASES.get(source.strip().lower())


def parse_filter(text: str) -> dict[str, Any]:
    """把筛选条件解析成结构化条件。

    返回 mode：
    - ``none``：空条件；
    - ``unfiltered``：只有说明性文字、没有结构化表达式，视为不限制成员；
    - ``filtered``：解析出可执行条件；
    - ``invalid``：看起来是表达式但无法解析。
    """
    raw = (text or "").strip()
    if not raw:
        return {"mode": "none", "conditions": [], "error": None}
    clauses = [item.strip() for item in _AND_RE.split(raw) if item.strip()]
    conditions: list[dict[str, Any]] = []
    for clause in clauses:
        match = _IN_RE.match(clause)
        if match:
            source, op, body = match.group(1), match.group(2).lower().replace("  ", " "), match.group(3)
            field = _canonical_field(source)
            if field is None:
                return {"mode": "invalid", "conditions": conditions,
                        "error": f"筛选条件引用了样本池中不存在的字段：{source}"}
            values = [_clean_value(item) for item in body.split(",") if item.strip()]
            if not values:
                return {"mode": "invalid", "conditions": conditions, "error": f"条件「{clause}」缺少取值"}
            conditions.append({
                "field": field, "source_field": source, "op": op, "value": values,
                "text": f"{source} {op} ({', '.join(values)})",
            })
            continue
        match = _CMP_RE.match(clause)
        if match and match.group(3) != "":
            source, op, value = match.group(1), match.group(2), _clean_value(match.group(3))
            field = _canonical_field(source)
            if field is None:
                return {"mode": "invalid", "conditions": conditions,
                        "error": f"筛选条件引用了样本池中不存在的字段：{source}"}
            if op in (">", ">=", "<", "<=") and field not in _NUMERIC_FIELDS:
                return {"mode": "invalid", "conditions": conditions,
                        "error": f"字段「{source}」不是数值字段，不能使用「{op}」比较"}
            if field in _NUMERIC_FIELDS:
                try:
                    float(value)
                except (TypeError, ValueError):
                    return {"mode": "invalid", "conditions": conditions,
                            "error": f"字段「{source}」的取值「{value}」不是有效数值"}
            conditions.append({
                "field": field, "source_field": source, "op": op, "value": value,
                "text": f"{source} {op} {value}",
            })
            continue
        if any(char in clause for char in "=<>"):
            return {"mode": "invalid", "conditions": conditions, "error": f"无法解析的条件：{clause}"}
        # 纯说明文字（历史数据里常见），不当作可执行条件。
    if not conditions:
        return {"mode": "unfiltered", "conditions": [], "error": None}
    return {"mode": "filtered", "conditions": conditions, "error": None}


def _norm(value: Any) -> str:
    return str(value).strip().casefold()


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op in (">", ">=", "<", "<="):
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        return left <= right
    if op in ("=", "=="):
        return _norm(actual) == _norm(_alias_value(actual, expected))
    if op == "!=":
        return _norm(actual) != _norm(_alias_value(actual, expected))
    if op == "in":
        return _norm(actual) in {_norm(_alias_value(actual, item)) for item in expected}
    if op == "not in":
        return _norm(actual) not in {_norm(_alias_value(actual, item)) for item in expected}
    return False


def _alias_value(actual: Any, expected: Any) -> Any:
    """把「对照组/实验组」这类中文取值归一成样本池里的取值。"""
    text = _norm(expected)
    for mapping in _VALUE_ALIASES.values():
        if text in mapping:
            return mapping[text]
    return expected


# ---------------------------------------------------------------------------
# 确定性样本池
# ---------------------------------------------------------------------------

def _hash_int(seed: str, *parts: Any) -> int:
    payload = "|".join([seed, *(str(part) for part in parts)])
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _parse_base(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class SampleGroupService:
    # -- 基础查询 ---------------------------------------------------------

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        with_members: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("分群编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        items: list[dict[str, Any]] = []
        for row in rows[start:start + size]:
            item = dict(row)
            if with_members:
                # 列表页与详情页读取同一份确认结果，列表只带摘要。
                item["member_summary"] = self.get_member_summary(int(row.get("id", 0)))
            items.append(item)
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # -- 成员明细 ---------------------------------------------------------

    def get_group_members(self, group_id: int) -> list[dict[str, Any]]:
        """兼容旧调用：返回确认结果里的成员列表（稳定顺序、稳定成员键）。"""
        snapshot = self.get_member_snapshot(group_id)
        if snapshot is None:
            return []
        return [dict(member) for member in snapshot.get("members", [])]

    def get_member_summary(self, group_id: int) -> dict[str, Any]:
        try:
            snapshot = self.get_member_snapshot(group_id)
        except Exception as error:  # 单条分群出错不能拖垮整张列表
            group = self.get_entry(group_id)
            return {
                "state": "failed",
                "state_label": STATE_LABELS["failed"],
                "ready": False,
                "reason_code": "summary_error",
                "reason": f"成员明细读取异常：{error}；该分群结果暂不可用，其余分群不受影响。",
                "retryable": True,
                "sample_base": _parse_base((group or {}).get("样本基数")),
                "member_limit": MEMBER_LIMIT,
                "matched_count": 0,
                "member_count": 0,
                "unexpanded_count": 0,
                "truncated": False,
                "snapshot_id": None,
                "version_tag": VERSION_TAG,
            }
        if snapshot is None:
            return {
                "state": "failed",
                "state_label": STATE_LABELS["failed"],
                "ready": False,
                "reason_code": "missing_group",
                "reason": f"样本分群 {group_id} 不存在或已归档，无法读取成员明细。",
                "retryable": False,
                "sample_base": None,
                "member_limit": MEMBER_LIMIT,
                "matched_count": 0,
                "member_count": 0,
                "unexpanded_count": 0,
                "truncated": False,
                "snapshot_id": None,
                "version_tag": VERSION_TAG,
            }
        return {
            key: snapshot.get(key)
            for key in (
                "state", "state_label", "ready", "reason_code", "reason", "retryable",
                "sample_base", "member_limit", "matched_count", "member_count",
                "unexpanded_count", "truncated", "snapshot_id", "version_tag", "computed_at",
                "filter_mode",
            )
        }

    def get_member_snapshot(self, group_id: int, *, force: bool = False) -> dict[str, Any] | None:
        """读取分群成员确认结果；没有可用快照时先计算，再返回同一份结果。"""
        group = self.get_entry(group_id)
        if group is None:
            return None
        with _LOCK:
            fingerprint = self._fingerprint(group)
            snapshot, members, residue = self._load_committed(group_id)
            if residue:
                # 核对明细表为空/条数对不上：说明上次事务留下了残行，整体回滚后再重算。
                self._rollback(group_id, f"核对明细表与快照不一致：{residue}", snapshot)
                snapshot, members = None, []
            if (
                not force
                and snapshot is not None
                and snapshot.get("fingerprint") == fingerprint
            ):
                # 可重试的失败在下次读取时自动重算一次；重算仍失败则原样返回，
                # 避免每次打开详情都反复触发计算，同时把原因留给页面展示。
                if snapshot.get("state") == "failed" and snapshot.get("retryable") and not snapshot.get("auto_retry_used"):
                    snapshot["auto_retry_used"] = True
                    return self._recompute(group, fingerprint, snapshot, members, forced=False)
                return self._with_members(snapshot, members)
            return self._recompute(group, fingerprint, snapshot, members, forced=force)

    def get_member_audit(self, group_id: int) -> dict[str, Any] | None:
        """V025 留档核对：返回确认结果、留档记录与残行检查。"""
        group = self.get_entry(group_id)
        if group is None:
            return None
        snapshot, members, residue = self._load_committed(group_id)
        audit_rows = [
            dict(row)
            for row in store.aux_rows(AUDIT_TABLE)
            if int(row.get("group_id", -1)) == group_id
        ]
        audit_rows.sort(key=lambda row: int(row.get("seq", 0)), reverse=True)
        latest = self._with_members(snapshot, members) if snapshot is not None else None
        return {
            "group_id": group_id,
            "version_tag": VERSION_TAG,
            "member_limit": MEMBER_LIMIT,
            "snapshot": latest,
            "summary": self.get_member_summary(group_id),
            "audit": audit_rows[:20],
            "residue": {
                "checked": True,
                "ok": residue is None,
                "detail": "核对明细表与快照一致" if residue is None else residue,
                "member_rows": len(members),
                "declared_member_count": int(snapshot.get("member_count", 0)) if snapshot else 0,
            },
        }

    # -- 状态流转 ---------------------------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["created_at"] = time.time()
        store.rows(MODULE).append(entry)
        store.bump_version(MODULE)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"样本分群 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于样本分群可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        note = ""
        if action in ("开始计算", "确认就绪"):
            snapshot = self.get_member_snapshot(entry_id, force=action == "开始计算")
            if snapshot is not None:
                note = str(snapshot.get("reason") or "")
                if action == "确认就绪" and snapshot.get("state") in BLOCKING_STATES:
                    return None, (
                        f"分群成员明细为「{snapshot.get('state_label')}」，不能确认就绪："
                        f"{snapshot.get('reason')}"
                    )
                if action == "开始计算" and snapshot.get("state") == "failed":
                    note = f"成员明细计算失败，保留最近完整成员集合：{note}"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.bump_version(MODULE)
        message = f"样本分群已{action}"
        if note:
            message = f"{message}；{note}"
        return entry, message

    # -- 内部：计算与提交 -------------------------------------------------

    def _recompute(
        self,
        group: dict[str, Any],
        fingerprint: str,
        previous: dict[str, Any] | None,
        previous_members: list[dict[str, Any]],
        *,
        forced: bool,
    ) -> dict[str, Any]:
        group_id = int(group.get("id", 0))
        try:
            snapshot, members = self._compute(group, fingerprint)
            self._validate(snapshot, members)
        except _ComputeError as error:
            self._append_audit(
                group_id,
                state="failed",
                accepted=False,
                rolled_back=True,
                reason_code=error.code,
                reason=error.reason,
                retryable=error.retryable,
                snapshot=None,
                error=error.reason,
            )
            return self._failure(group, previous, previous_members,
                                 code=error.code, reason=error.reason,
                                 retryable=error.retryable)
        except Exception as error:  # 生成过程出错：不提交，保持最近完整集合
            reason = f"成员明细计算异常：{error}"
            self._append_audit(
                group_id,
                state="failed",
                accepted=False,
                rolled_back=True,
                reason_code="compute_error",
                reason=reason,
                retryable=True,
                snapshot=None,
                error=repr(error),
            )
            return self._failure(group, previous, previous_members,
                                 code="compute_error", reason=reason, retryable=True)

        snapshot["module_version"] = store.version(MODULE)
        # 只有核对通过后才整体替换：先写明细，再写快照，读到的永远是完整一组。
        store.replace_aux_rows(MEMBER_TABLE, lambda row: int(row.get("group_id", -1)) == group_id, members)
        store.replace_aux_rows(SNAPSHOT_TABLE, lambda row: int(row.get("group_id", -1)) == group_id, [snapshot])
        self._append_audit(
            group_id,
            state=snapshot["state"],
            accepted=True,
            rolled_back=False,
            reason_code=snapshot["reason_code"],
            reason=snapshot["reason"],
            retryable=snapshot.get("retryable", False),
            snapshot=snapshot,
        )
        if forced:
            snapshot["forced"] = True
        return self._with_members(snapshot, members)

    def _compute(self, group: dict[str, Any], fingerprint: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        group_id = int(group.get("id", 0))
        raw_base = group.get("样本基数")
        base = _parse_base(raw_base)
        filter_text = str(group.get("筛选条件") or "")
        parsed = parse_filter(filter_text)
        seed = self._seed(group)
        experiment_id = group.get("experiment_id")
        group_type = str(group.get("group_type") or "")

        snapshot_id = f"{VERSION_TAG}-{group_id}-{self._next_seq():08d}"
        common = {
            "group_id": group_id,
            "snapshot_id": snapshot_id,
            "seq": int(snapshot_id.rsplit("-", 1)[-1]),
            "version_tag": VERSION_TAG,
            "fingerprint": fingerprint,
            "group_version": store.version(MODULE),
            "filter_text": filter_text,
            "filter_mode": parsed["mode"],
            "filter_conditions": parsed["conditions"],
            "filter_error": parsed.get("error"),
            "sample_base": base,
            "member_limit": MEMBER_LIMIT,
            "computed_at": time.time(),
        }

        if base is None:
            return self._state_snapshot(
                common, state="no_data", code="no_sample_base",
                reason=(
                    f"分群未提供有效样本基数（当前值：{raw_base!r}），无法计算成员明细；"
                    "请先补充样本基数后重新计算。"
                ),
                retryable=False,
            ), []

        if parsed["mode"] == "invalid":
            return self._state_snapshot(
                common, state="invalid", code="invalid_filter",
                reason=(
                    f"筛选条件「{filter_text}」无法解析：{parsed.get('error')}；"
                    "本次不生成成员明细，避免把条件错误当成空群结果。"
                ),
                retryable=False,
            ), []

        if parsed["mode"] == "filtered" and base > SCAN_LIMIT:
            raise _ComputeError(
                "scan_limit",
                f"筛选条件下候选量 {base} 超过可核对上限 {SCAN_LIMIT}，"
                "为避免静默截断，本次不返回成员明细；请先收紧筛选条件。",
                retryable=False,
            )

        members: list[dict[str, Any]] = []
        matched = 0
        if parsed["mode"] in ("none", "unfiltered"):
            matched = base
            for index in range(min(base, MEMBER_LIMIT)):
                members.append(self._member(seed, group_id, group_type, experiment_id, index, index + 1, snapshot_id))
        else:
            # 只对前 MEMBER_LIMIT 条命中记录逐条核对成员键与指标值；
            # 超过上限后不再生成明细，未展开数量由 matched_count - MEMBER_LIMIT 解释。
            kept: list[int] = []
            for index in range(base):
                attrs = self._candidate(seed, group_id, group_type, experiment_id, index)
                if all(_compare(attrs.get(cond["field"]), cond["op"], cond["value"]) for cond in parsed["conditions"]):
                    matched += 1
                    if len(kept) < MEMBER_LIMIT:
                        kept.append(index)
            for ordinal, index in enumerate(kept, start=1):
                members.append(self._member(seed, group_id, group_type, experiment_id, index, ordinal, snapshot_id))

        if matched == 0:
            return self._state_snapshot(
                common, state="empty", code="zero_matched",
                reason=(
                    f"筛选条件「{filter_text}」命中 0 名成员，按空群返回：成员明细为空，"
                    f"样本基数仍记录为 {base}，该结果不能当作已就绪分群使用。"
                ),
                retryable=False,
                matched=0,
                members=members,
            ), members
        if matched > MEMBER_LIMIT:
            return self._state_snapshot(
                common, state="truncated", code="member_limit",
                reason=(
                    f"筛选条件命中 {matched} 名成员，超过成员上限 {MEMBER_LIMIT}；"
                    f"明细展示前 {MEMBER_LIMIT} 条，另有 {matched - MEMBER_LIMIT} 条未展开，"
                    f"样本基数保持 {base} 不变。"
                ),
                retryable=False,
                matched=matched,
                members=members,
                unexpanded=matched - MEMBER_LIMIT,
            ), members
        return self._state_snapshot(
            common, state="ready", code="confirmed",
            reason=(
                f"筛选条件命中 {matched} 名成员，样本基数 {base}，"
                f"成员明细已按 {VERSION_TAG} 核对确认。"
            ),
            retryable=False,
            matched=matched,
            members=members,
        ), members

    def _state_snapshot(
        self,
        common: dict[str, Any],
        *,
        state: str,
        code: str,
        reason: str,
        retryable: bool,
        matched: int = 0,
        members: list[dict[str, Any]] | None = None,
        unexpanded: int = 0,
    ) -> dict[str, Any]:
        rows = members or []
        snapshot = dict(common)
        snapshot.update({
            "state": state,
            "state_label": STATE_LABELS[state],
            "ready": state in READY_STATES,
            "retryable": retryable,
            "reason_code": code,
            "reason": reason,
            "matched_count": matched,
            "member_count": len(rows),
            "unexpanded_count": unexpanded,
            "truncated": state == "truncated",
            "member_checksum": self._checksum(rows),
            "accepted": True,
        })
        return snapshot

    def _member(
        self,
        seed: str,
        group_id: int,
        group_type: str,
        experiment_id: Any,
        index: int,
        ordinal: int,
        snapshot_id: str,
    ) -> dict[str, Any]:
        user_id = f"U{group_id}{index:05d}"
        # 指标值只由分群身份、筛选口径与用户下标决定，刷新/重算都不会漂移。
        noise = (_hash_int(seed, "metric", index) / float(1 << 64)) * 2.0 - 1.0
        mean = 0.62 if group_type == "experiment" else 0.55
        value = max(0.0, min(1.0, mean + noise * 0.22))
        return {
            "snapshot_id": snapshot_id,
            "group_id": group_id,
            "ordinal": ordinal,
            "member_key": f"{group_id}:{user_id}",
            "user_id": user_id,
            "group_type": group_type or "all",
            "metric_value": round(value, 4),
        }

    def _candidate(
        self,
        seed: str,
        group_id: int,
        group_type: str,
        experiment_id: Any,
        index: int,
    ) -> dict[str, Any]:
        def bucket(field: str, size: int) -> int:
            return _hash_int(seed, field, index) % size

        return {
            "user_id": f"U{group_id}{index:05d}",
            # 分群没有声明实验归属时不伪造 experiment_id，避免条件碰巧命中分群自身的 id。
            "experiment_id": str(experiment_id) if experiment_id not in (None, "") else None,
            "group": group_type or "all",
            "platform": _PLATFORMS[bucket("platform", len(_PLATFORMS))],
            "country": _COUNTRIES[bucket("country", len(_COUNTRIES))],
            "city": _CITIES[bucket("city", len(_CITIES))],
            "device": _DEVICES[bucket("device", len(_DEVICES))],
            "age": 18 + bucket("age", 43),
            "register_days": bucket("register_days", 365),
            "active_days": bucket("active_days", 30),
        }

    # -- 内部：核对、快照读写与留档 ---------------------------------------

    def _validate(self, snapshot: dict[str, Any], members: list[dict[str, Any]]) -> None:
        """提交前核对明细表与快照声明，任何不一致都整体回滚。"""
        errors: list[str] = []
        matched = int(snapshot.get("matched_count") or 0)
        declared = int(snapshot.get("member_count") or 0)
        base = snapshot.get("sample_base")
        unexpanded = int(snapshot.get("unexpanded_count") or 0)
        if declared != len(members):
            errors.append(f"声明成员数 {declared} 与明细表 {len(members)} 条不一致")
        if base is not None and matched > int(base):
            errors.append(f"有效成员数 {matched} 超过样本基数 {base}")
        if declared != min(matched, MEMBER_LIMIT):
            errors.append(f"明细条数 {declared} 不等于 min(命中数 {matched}, 上限 {MEMBER_LIMIT})")
        if unexpanded != max(matched - MEMBER_LIMIT, 0):
            errors.append(f"未展开数量 {unexpanded} 与命中数/上限不符")
        keys = [member.get("member_key") for member in members]
        if len(set(keys)) != len(keys):
            errors.append("成员键存在重复")
        if any(int(member.get("group_id", -1)) != int(snapshot.get("group_id", -2)) for member in members):
            errors.append("明细中存在不属于当前分群的成员")
        if any(member.get("snapshot_id") != snapshot.get("snapshot_id") for member in members):
            errors.append("明细中存在不属于当前快照的残留行")
        if self._checksum(members) != snapshot.get("member_checksum"):
            errors.append("成员明细校验和与快照不一致")
        if snapshot.get("state") in ("empty", "no_data", "invalid", "failed") and members:
            errors.append(f"状态 {snapshot.get('state')} 不应携带成员明细")
        if errors:
            raise _ComputeError(
                "integrity_check",
                "提交前核对失败，已整体回滚：" + "；".join(errors),
                retryable=False,
            )

    def _load_committed(
        self, group_id: int
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
        snapshots = [
            row for row in store.aux_rows(SNAPSHOT_TABLE)
            if int(row.get("group_id", -1)) == group_id
        ]
        snapshot = max(snapshots, key=lambda row: int(row.get("seq", 0))) if snapshots else None
        if snapshot is None:
            leftovers = [
                row for row in store.aux_rows(MEMBER_TABLE)
                if int(row.get("group_id", -1)) == group_id
            ]
            if leftovers:
                return None, [], f"快照为空但明细表残留 {len(leftovers)} 条成员"
            return None, [], None
        members = [
            dict(row) for row in store.aux_rows(MEMBER_TABLE)
            if row.get("snapshot_id") == snapshot.get("snapshot_id")
        ]
        members.sort(key=lambda row: int(row.get("ordinal", 0)))
        # 同一分群下不属于当前快照的明细行都是上次事务残留，必须整体回滚。
        stale = [
            row for row in store.aux_rows(MEMBER_TABLE)
            if int(row.get("group_id", -1)) == group_id
            and row.get("snapshot_id") != snapshot.get("snapshot_id")
        ]
        if stale:
            return snapshot, members, (
                f"核对明细表发现 {len(stale)} 条不属于当前快照的残留成员，需整体回滚后重算"
            )
        if len(members) != int(snapshot.get("member_count", 0)):
            return snapshot, members, (
                f"快照声明 {snapshot.get('member_count')} 条成员，核对明细表实际 {len(members)} 条"
            )
        if self._checksum(members) != snapshot.get("member_checksum"):
            return snapshot, members, "核对明细表校验和与快照不一致"
        return snapshot, members, None

    def _rollback(self, group_id: int, reason: str, snapshot: dict[str, Any] | None) -> None:
        """清掉同一分群的快照与明细，避免残留行被后续读取当成完整结果。"""
        store.clear_aux_rows(MEMBER_TABLE, lambda row: int(row.get("group_id", -1)) == group_id)
        store.clear_aux_rows(SNAPSHOT_TABLE, lambda row: int(row.get("group_id", -1)) == group_id)
        self._append_audit(
            group_id,
            state="failed",
            accepted=False,
            rolled_back=True,
            reason_code="residue_rollback",
            reason=reason,
            retryable=True,
            snapshot=snapshot,
            error=reason,
        )

    def _failure(
        self,
        group: dict[str, Any],
        previous: dict[str, Any] | None,
        previous_members: list[dict[str, Any]],
        *,
        code: str,
        reason: str,
        retryable: bool,
    ) -> dict[str, Any]:
        """计算失败：不提交新结果，把失败信息挂到最近完整快照上。

        成员明细仍然返回最近一次核对通过的集合，但状态明确标为「计算失败」，
        不会继续冒充已就绪；若从未成功过，则落一份无成员的失败快照，避免每次
        读取都重复计算。
        """
        group_id = int(group.get("id", 0))
        # 上一次快照还带着成员明细时，才能说「保留完整成员集合」。
        keep_previous = previous is not None and (
            previous.get("state") in READY_STATES or bool(previous_members)
        )
        if keep_previous:
            message = reason + "；已保留最近一次完整成员集合，核对通过后可重试。"
        elif previous is not None:
            message = reason + "；上一次结果也不是完整成员集合，本次未提交新结果。"
        else:
            message = reason + "；当前没有完整成员集合，明细为空。"
        if previous is not None:
            stored = next(
                (
                    row for row in store.aux_rows(SNAPSHOT_TABLE)
                    if row.get("snapshot_id") == previous.get("snapshot_id")
                ),
                None,
            )
            base = dict(previous)
            base["last_complete_state"] = previous.get("last_complete_state") or previous.get("state")
            base["last_complete_snapshot_id"] = (
                previous.get("last_complete_snapshot_id") or previous.get("snapshot_id")
            )
            base.update({
                "state": "failed",
                "state_label": STATE_LABELS["failed"],
                "ready": False,
                "reason_code": code,
                "reason": message,
                "retryable": retryable,
                "computed_at": time.time(),
            })
            if stored is not None:
                stored.update({
                    key: base[key] for key in (
                        "state", "state_label", "ready", "reason_code", "reason",
                        "retryable", "computed_at", "last_complete_state",
                        "last_complete_snapshot_id",
                    )
                })
            return self._with_members(base, previous_members)

        snapshot = {
            "group_id": group_id,
            "snapshot_id": f"{VERSION_TAG}-{group_id}-{self._next_seq():08d}",
            "seq": self._next_seq(),
            "version_tag": VERSION_TAG,
            "fingerprint": self._fingerprint(group),
            "group_version": store.version(MODULE),
            "filter_text": str(group.get("筛选条件") or ""),
            "filter_mode": "unknown",
            "filter_conditions": [],
            "filter_error": None,
            "sample_base": _parse_base(group.get("样本基数")),
            "member_limit": MEMBER_LIMIT,
            "matched_count": 0,
            "member_count": 0,
            "unexpanded_count": 0,
            "truncated": False,
            "member_checksum": self._checksum([]),
            "accepted": False,
            "state": "failed",
            "state_label": STATE_LABELS["failed"],
            "ready": False,
            "reason_code": code,
            "reason": message,
            "retryable": retryable,
            "computed_at": time.time(),
            "last_complete_state": None,
            "last_complete_snapshot_id": None,
            "members": [],
        }
        store.replace_aux_rows(MEMBER_TABLE, lambda row: int(row.get("group_id", -1)) == group_id, [])
        store.replace_aux_rows(SNAPSHOT_TABLE, lambda row: int(row.get("group_id", -1)) == group_id, [snapshot])
        return snapshot

    def _with_members(self, snapshot: dict[str, Any], members: Iterable[dict[str, Any]]) -> dict[str, Any]:
        result = dict(snapshot)
        result["members"] = [dict(member) for member in members]
        result.setdefault("state_label", STATE_LABELS.get(str(result.get("state")), "未知"))
        return result

    def _append_audit(
        self,
        group_id: int,
        *,
        state: str,
        accepted: bool,
        rolled_back: bool,
        reason_code: str,
        reason: str,
        retryable: bool,
        snapshot: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        seq = self._next_seq()
        row = {
            "seq": seq,
            "group_id": group_id,
            "snapshot_id": (snapshot or {}).get("snapshot_id"),
            "version_tag": VERSION_TAG,
            "state": state,
            "state_label": STATE_LABELS.get(state, state),
            "accepted": accepted,
            "rolled_back": rolled_back,
            "retryable": retryable,
            "reason_code": reason_code,
            "reason": reason,
            "sample_base": (snapshot or {}).get("sample_base"),
            "member_limit": MEMBER_LIMIT,
            "matched_count": (snapshot or {}).get("matched_count", 0),
            "member_count": (snapshot or {}).get("member_count", 0),
            "unexpanded_count": (snapshot or {}).get("unexpanded_count", 0),
            "filter_text": (snapshot or {}).get("filter_text"),
            "computed_at": time.time(),
            "error": error,
        }
        table = store.aux_rows(AUDIT_TABLE)
        table.append(row)
        # 留档只保留最近 20 次，避免内存无限增长。
        group_rows = [item for item in table if int(item.get("group_id", -1)) == group_id]
        if len(group_rows) > 20:
            stale = {item["seq"] for item in sorted(group_rows, key=lambda item: item["seq"])[: len(group_rows) - 20]}
            table[:] = [item for item in table if item.get("seq") not in stale]

    def _fingerprint(self, group: dict[str, Any]) -> str:
        fields = ("id", "分群编号", "分群名称", "筛选条件", "样本基数", "group_type", "experiment_id", "status")
        payload = "|".join(f"{field}={group.get(field)}" for field in fields)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _seed(self, group: dict[str, Any]) -> str:
        payload = "|".join([
            VERSION_TAG,
            str(group.get("id", "")),
            str(group.get("分群编号", "")),
            str(group.get("筛选条件", "")),
            str(group.get("样本基数", "")),
            str(group.get("group_type", "")),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _checksum(self, members: Iterable[dict[str, Any]]) -> str:
        payload = "|".join(
            f"{member.get('member_key')}:{member.get('metric_value')}"
            for member in members
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _next_seq(self) -> int:
        global _SEQ
        with _LOCK:
            _SEQ += 1
            return _SEQ
