"""杂草清除业务规则：状态流转、字段落库、幂等安排与看板口径都收在这里。

状态只能沿作业顺序一步一步往前走：
待安排 → 已安排 → 清除中 → 待复核 →（复核驳回）需补除 → 待复核 →（复核通过）已清除
任何后退（例如已安排退回待安排）、跳步（例如待安排直接登记结果）都会被拦下。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.store import store

MODULE = "weed"

# 登记时必须给的字段；其余业务字段可选但一旦提交就必须落库，不能静默丢弃。
REQUIRED_FIELDS = ["除草编号", "除草区域", "杂草种类"]
OPTIONAL_FIELDS = ["覆盖程度", "除草方式", "作业日期", "作业人员", "作业面积"]
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

# 状态序列（顺序即允许的前进方向）
STATUS_PENDING = "待安排"
STATUS_SCHEDULED = "已安排"
STATUS_CLEARING = "清除中"
STATUS_REVIEW = "待复核"
STATUS_RESOW = "需补除"
STATUS_DONE = "已清除"
STATUS_ORDER = [
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_CLEARING,
    STATUS_REVIEW,
    STATUS_RESOW,
    STATUS_DONE,
]
# 终态：已清完，待办里不再出现
DONE_STATUSES = {STATUS_DONE}
# 需补除也算"还有活在同一片区域上"，安排时用于区域去重
ACTIVE_STATUSES = set(STATUS_ORDER) - DONE_STATUSES
# 登记清除结果时必须取得的结果字段；取不到结果时允许重新取一次接着走
RESULT_FIELDS = ["覆盖程度", "作业面积"]

# 动作 -> (目标状态, 唯一允许的前置状态)；前置状态保证状态只能一步步往前走
ACTION_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "安排除草": (STATUS_SCHEDULED, (STATUS_PENDING,)),
    "开始清除": (STATUS_CLEARING, (STATUS_SCHEDULED,)),
    "登记清除结果": (STATUS_REVIEW, (STATUS_CLEARING,)),
    "重新获取结果": (STATUS_REVIEW, (STATUS_CLEARING,)),
    "补除登记": (STATUS_REVIEW, (STATUS_RESOW,)),
    "复核驳回": (STATUS_RESOW, (STATUS_REVIEW,)),
    "复核通过": (STATUS_DONE, (STATUS_REVIEW,)),
}
# 会把作业标记成异常的动作（看板"异常量"取这个口径）
NEGATIVE_ACTIONS = {"复核驳回"}


class WeedService:
    # ---------- 读取 ----------
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
        # 列表与详情走同一份序列化结果，保证清单页和详情页字段一致
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row is not None else None

    def review_queue(self) -> list[dict[str, Any]]:
        """待复核清单：清除结果登记后会落到这里，等复核给出结论。"""
        rows = store.rows(MODULE)
        return [self._present(row) for row in rows if row.get("status") == STATUS_REVIEW]

    def board_stats(self) -> dict[str, Any]:
        """清除看板：所有指标每次都按明细重算，刷新后与清单保持一致。"""
        rows = store.rows(MODULE)
        counts = {status: 0 for status in STATUS_ORDER}
        done_area = 0.0
        review_area = 0.0
        for row in rows:
            status = str(row.get("status") or "")
            if status in counts:
                counts[status] += 1
            if status == STATUS_DONE:
                done_area += self._to_number(row.get("作业面积"))
            if status == STATUS_REVIEW:
                review_area += self._to_number(row.get("作业面积"))
        return {
            "total": len(rows),
            "counts": counts,
            "待安排": counts[STATUS_PENDING],
            "已安排": counts[STATUS_SCHEDULED],
            "清除中": counts[STATUS_CLEARING],
            "待复核": counts[STATUS_REVIEW],
            "需补除": counts[STATUS_RESOW],
            "已清除": counts[STATUS_DONE],
            "完成面积": round(done_area, 2),
            "待复核面积": round(review_area, 2),
        }

    # ---------- 写入 ----------
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False

        area = str(values.get("除草区域") or "").strip()
        rows = store.rows(MODULE)
        # 同一片区域重复提交安排除草只生效一次：已有未完工记录时直接回它
        active = self._find_active_by_area(rows, area)
        if active is not None:
            return self._present(active), [], False

        entry: dict[str, Any] = {"id": self._next_id(rows)}
        for field in ALL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip() != "":
                entry[field] = str(value).strip()
        entry["status"] = STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._present(entry), [], True

    def auto_schedule(self) -> dict[str, Any]:
        """把待安排的除草任务自动排进作业单。

        同一片区域只生效一次：已安排/清除中/待复核/需补除的区域跳过；
        已经清完的区域不再自动排进去（清完之后新登记的下一轮记录除外）。
        """
        rows = store.rows(MODULE)
        scheduled: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in rows:
            if row.get("status") != STATUS_PENDING:
                continue
            area = str(row.get("除草区域") or "").strip()
            reason = ""
            if any(other is not row and other.get("status") in ACTIVE_STATUSES
                   and str(other.get("除草区域") or "").strip() == area
                   for other in rows):
                reason = "该区域已有在途除草安排"
            elif self._was_area_cleared_after(rows, area, int(row.get("id", 0))):
                reason = "该区域已清除完成"
            if reason:
                skipped.append({"id": row.get("id"), "除草区域": area, "原因": reason})
                continue
            self._advance(row, "安排除草", {})
            scheduled.append(self._present(row))
        return {
            "scheduled": scheduled,
            "scheduled_count": len(scheduled),
            "skipped": skipped,
            "skipped_count": len(skipped),
        }

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"除草任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于杂草清除可执行范围"

        target, allowed_from = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current not in allowed_from:
            allowed_text = "、".join(allowed_from)
            return None, f"当前状态为「{current}」，动作「{action}」只能在{allowed_text}状态下执行，状态不能后退或跳步"

        # 安排除草：同一片区域已有在途记录时不重复生效；已清完的也不能被旧记录带回去
        if action == "安排除草":
            rows = store.rows(MODULE)
            dup = self._find_active_by_area(
                rows, str(entry.get("除草区域") or "").strip(), exclude_id=entry_id
            )
            if dup is not None:
                return self._present(dup), "同一片区域已有在途除草安排，本次提交未重复生效"

        # 清除结果类动作：清完之后的覆盖程度、除草方式等结果字段必须能取到并落库
        if action in ("开始清除", "登记清除结果", "重新获取结果", "补除登记"):
            values = values or {}
            merged = {field: values[field] for field in ALL_FIELDS if field in values}
            if action in ("登记清除结果", "重新获取结果", "补除登记"):
                # 结果必须是本次新取到的值；清之前登记的老覆盖率不能拿来充数
                missing = [
                    field for field in RESULT_FIELDS
                    if not str(values.get(field) or "").strip()
                ]
                if missing:
                    # 取不到结果不改动状态，允许重新取一次接着走
                    return None, f"清除结果缺少{'、'.join(missing)}，暂未落库，请重新获取结果后再提交"
            self._apply_values(entry, merged)
            entry.setdefault("作业日期", date.today().isoformat())
        elif values:
            self._apply_values(entry, {
                field: values[field] for field in ALL_FIELDS if field in values
            })

        self._advance(entry, action, values)
        return self._present(entry), f"除草任务已{action}"

    # ---------- 内部工具 ----------
    def _advance(self, entry: dict[str, Any], action: str, values: dict[str, Any] | None) -> None:
        target, _ = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target not in DONE_STATUSES
        entry["abnormal"] = target == STATUS_RESOW or action in NEGATIVE_ACTIONS
        if action in ("登记清除结果", "重新获取结果", "补除登记") and values:
            entry["复核状态"] = "待复核"

    def _apply_values(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        # 清完/补除后登记的覆盖程度、除草方式、作业日期、作业人员等全部覆盖落库
        for field, value in values.items():
            text = str(value or "").strip()
            if text:
                entry[field] = text

    def _find_active_by_area(
        self, rows: list[dict[str, Any]], area: str, exclude_id: int | None = None
    ) -> dict[str, Any] | None:
        for row in rows:
            if exclude_id is not None and int(row.get("id", 0)) == exclude_id:
                continue
            if str(row.get("除草区域") or "").strip() == area and row.get("status") in ACTIVE_STATUSES:
                return row
        return None

    def _was_area_cleared_after(self, rows: list[dict[str, Any]], area: str, entry_id: int) -> bool:
        """该区域在本条记录登记之后已经清完（旧待安排记录不应把清完的区域重新排进来）。"""
        return any(
            str(row.get("除草区域") or "").strip() == area
            and row.get("status") in DONE_STATUSES
            and int(row.get("id", 0)) > entry_id
            for row in rows
        )

    def _next_id(self, rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def _to_number(self, value: Any) -> float:
        # "12.5 亩" / "12㎡" 都按开头的数值算；取不到数值按 0 处理
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group()) if match else 0.0

    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """统一的对外视图：列表和详情共用，杂草种类、覆盖程度等字段不会再对不上。"""
        item: dict[str, Any] = {"id": row.get("id"), "除草状态": row.get("status")}
        for field in ALL_FIELDS:
            item[field] = row.get(field)
        item.update({
            "status": row.get("status"),
            "pending": row.get("pending", row.get("status") not in DONE_STATUSES),
            "abnormal": row.get("abnormal", row.get("status") == STATUS_RESOW),
            "完成面积": self._to_number(row.get("作业面积")) if row.get("status") == STATUS_DONE else 0,
        })
        return item
