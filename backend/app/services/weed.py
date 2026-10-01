"""杂草清除业务规则：字段口径、状态流转、复核清单与看板统计都收在这里。

设计要点：
- 状态只能按既定动作单向往前走：待清除 → 清除中 → 已清除 → 需补除 → 已清除，
  没有任何动作能回到「待清除」，已安排的任务不可能退回待安排。
- 安排除草对同一片区域幂等：重复提交只生效一次；区域已经清完且仍在待复核
  清单里的，不再重复排单。
- 清除结果（覆盖程度、作业面积、作业日期、作业人员、除草方式）随动作落库，
  并进入待复核清单；作业日期变动时，除草方式按季节联动。
- 看板的完成面积不存快照，每次都从明细实时重算。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "weed"
REQUIRED_FIELDS = ["除草编号", "除草区域", "杂草种类"]
DETAIL_FIELDS = ["覆盖程度", "除草方式", "作业日期", "作业人员", "作业面积"]
ALL_FIELDS = REQUIRED_FIELDS + DETAIL_FIELDS

# 状态序列：索引递增即「往前走」。需补除是收尾返工态，补除完成后回到已清除。
STATUS_ORDER = ["待清除", "清除中", "已清除", "需补除"]

# 动作 -> (允许的前置状态, 目标状态)。每个转换只允许单步，且不可回退。
ACTION_RULES: dict[str, tuple[str, str]] = {
    "安排除草": ("待清除", "清除中"),
    "开始清除": ("清除中", "已清除"),
    "补除登记": ("已清除", "需补除"),
    "补除完成": ("需补除", "已清除"),
}
# 进入已清除的动作：清除结果落库并进待复核清单。
FINISH_ACTIONS = {"开始清除", "补除完成"}

# 提交字段的别名归一：清单页/详情页/外部系统叫法不一，统一到内部口径，
# 避免同一条记录在两个页面看到两份数据。
FIELD_ALIASES = {
    "覆盖率": "覆盖程度",
    "覆盖度": "覆盖程度",
    "清除面积": "作业面积",
    "完成面积": "作业面积",
}

# 还在作业流程里（没有清完）的状态。
ACTIVE_STATUSES = {"待清除", "清除中", "需补除"}


def derive_method(work_date: str) -> str:
    """按作业日期所在季节给出默认除草方式。

    春发草芽以人工为主，夏季草旺用药剂，秋季结籽前机械刈割，冬季覆盖抑草。
    日期无法解析时不臆测，留给人工补填。
    """
    match = re.search(r"(\d{4})[-/.年](\d{1,2})", str(work_date or ""))
    if not match:
        return ""
    month = int(match.group(2))
    if month in (3, 4, 5):
        return "人工除草"
    if month in (6, 7, 8):
        return "化学除草"
    if month in (9, 10, 11):
        return "机械除草"
    return "覆盖抑草"


def parse_area(value: Any) -> float:
    """从「12.5 亩」「8m²」这类填报值里取出面积数值；取不到按 0 处理。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else 0.0


class WeedService:
    # ---- 读取 -----------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("除草编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def review_queue(self) -> list[dict[str, Any]]:
        """待复核清单：已登记清除结果、等待复核确认的任务。"""
        return [row for row in store.rows(MODULE) if row.get("pending_review")]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def stats(self) -> dict[str, Any]:
        """清除看板：所有数字每次都从明细重算，不读任何缓存快照。"""
        rows = store.rows(MODULE)
        counts = {status: 0 for status in STATUS_ORDER}
        completed_area = 0.0
        for row in rows:
            status = str(row.get("status") or "")
            if status in counts:
                counts[status] += 1
            if status == "已清除":
                completed_area += parse_area(row.get("作业面积"))
        return {
            "待清除": counts["待清除"],
            "清除中": counts["清除中"],
            "已清除": counts["已清除"],
            "需补除": counts["需补除"],
            "待复核": sum(1 for row in rows if row.get("pending_review")),
            "完成面积": round(completed_area, 2),
        }

    # ---- 写入 -----------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        data = self._canonicalize(values)
        missing = [field for field in REQUIRED_FIELDS if not str(data.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"

        area = str(data["除草区域"]).strip()
        duplicate = self._find_open_by_area(area)
        if duplicate is not None:
            status = duplicate.get("status")
            if status == "清除中":
                return None, f"「{area}」已安排除草（{duplicate.get('除草编号')}），重复提交只生效一次"
            if status == "已清除":
                return None, f"「{area}」已清完并在待复核清单中，不再重复排单"
            return None, f"「{area}」已有除草任务（{duplicate.get('除草编号')}），请勿重复登记"

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ALL_FIELDS:
            if data.get(field) not in (None, ""):
                entry[field] = data[field]
        entry["status"] = STATUS_ORDER[0]
        entry["除草状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["pending_review"] = False
        rows.append(entry)
        return entry, ""

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改明细字段（如补录覆盖程度、订正作业日期）；状态不允许通过这个入口改动。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"除草任务 {entry_id} 不存在或已归档"
        data = self._canonicalize(values)
        date_changed = "作业日期" in data and "除草方式" not in data
        for field in ALL_FIELDS:
            if field in data:
                entry[field] = data[field]
        self._sync_entry(entry, rederive_method=date_changed)
        return entry, ""

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"除草任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于杂草清除可执行范围"

        required_status, target = ACTION_RULES[action]
        current = str(entry.get("status") or "")

        # 幂等：上一次请求没取到结果、又点了一次，不重复生效，直接接着往下走。
        if current == target:
            return entry, f"除草任务已{action}，请勿重复操作"

        if current != required_status:
            reason = self._illegal_reason(action, current, required_status)
            return None, reason

        data = self._canonicalize(values or {})
        date_changed = "作业日期" in data and "除草方式" not in data
        for field in DETAIL_FIELDS:
            if field in data:
                entry[field] = data[field]
        self._sync_entry(entry, rederive_method=date_changed)

        entry["status"] = target
        entry["除草状态"] = target
        if action in FINISH_ACTIONS:
            # 清除结果落库，进入待复核清单。
            entry["pending_review"] = True
            entry["abnormal"] = False
        elif action == "补除登记":
            # 复核不通过、转补除作业：离开复核清单，标记异常返工。
            entry["pending_review"] = False
            entry["abnormal"] = True
        else:
            entry["abnormal"] = False
        # 已清除且复核通过才算结案；需补除/在作业/待复核都算待处理。
        entry["pending"] = target != "已清除" or bool(entry.get("pending_review"))
        return entry, f"除草任务已{action}"

    def review_entry(self, entry_id: int, passed: bool) -> tuple[dict[str, Any] | None, str]:
        """待复核清单的复核结论：通过则结案，不通过则转补除登记。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"除草任务 {entry_id} 不存在或已归档"
        if not entry.get("pending_review"):
            return None, "该任务不在待复核清单中，无需复核"
        if not passed:
            return self.run_action(entry_id, "补除登记")
        entry["pending_review"] = False
        entry["pending"] = False
        return entry, "复核通过，除草任务已结案"

    # ---- 内部规则 -------------------------------------------------------
    def _canonicalize(self, values: dict[str, Any]) -> dict[str, Any]:
        """归一提交口径：别名转正名、去空白、作业日期联动除草方式。"""
        data: dict[str, Any] = {}
        for key, value in (values or {}).items():
            name = FIELD_ALIASES.get(str(key).strip(), str(key).strip())
            if name in ALL_FIELDS:
                data[name] = value
        for key, value in list(data.items()):
            if isinstance(value, str):
                data[key] = value.strip()

        work_date = data.get("作业日期")
        if work_date:
            # 显式带了除草方式以填报为准；只动了日期时方式按季节联动。
            if not data.get("除草方式"):
                method = derive_method(str(work_date))
                if method:
                    data["除草方式"] = method
        return data

    def _sync_entry(self, entry: dict[str, Any], *, rederive_method: bool = False) -> None:
        """保证两个状态字段同口径，并对缺失的日期/方式做联动补齐。"""
        status = str(entry.get("status") or STATUS_ORDER[0])
        entry["status"] = status
        entry["除草状态"] = status
        work_date = entry.get("作业日期")
        if not work_date:
            return
        # 改了日期且没显式给方式：方式跟着日期重算；否则只在方式空缺时补默认值。
        if rederive_method:
            method = derive_method(str(work_date))
            if method:
                entry["除草方式"] = method
        elif not entry.get("除草方式"):
            method = derive_method(str(work_date))
            if method:
                entry["除草方式"] = method

    def _find_open_by_area(self, area: str) -> dict[str, Any] | None:
        """同一片区域还挂着未结案任务（作业中，或已清完但仍待复核）的记录。"""
        for row in store.rows(MODULE):
            if str(row.get("除草区域") or "").strip() != area:
                continue
            status = str(row.get("status") or "")
            if status in ACTIVE_STATUSES:
                return row
            if status == "已清除" and row.get("pending_review"):
                return row
        return None

    def _illegal_reason(self, action: str, current: str, required: str) -> str:
        target = ACTION_RULES[action][1]
        cur_idx = STATUS_ORDER.index(current) if current in STATUS_ORDER else -1
        req_idx = STATUS_ORDER.index(required)
        if cur_idx >= 0 and cur_idx > req_idx:
            return f"任务当前为「{current}」，不能回退到「{target}」，清除状态只能往前走"
        return f"「{action}」要求任务处于「{required}」，当前为「{current}」，请按顺序逐步推进"
