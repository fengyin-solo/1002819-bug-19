"""杂草清除接口：维护除草任务、清除结果登记、状态流转与待复核清单。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.weed import STATUS_ORDER, WeedService

router = APIRouter(prefix="/api/weed", tags=["杂草清除"])

service = WeedService()

LIST_FIELDS = ["除草编号", "除草区域", "杂草种类", "覆盖程度", "除草方式", "作业日期", "作业人员", "作业面积", "除草状态"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按除草编号检索"),
    status: str | None = Query(default=None, description="待清除、清除中、已清除、需补除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按除草编号与状态过滤杂草清除列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats", response_model=dict)
def weed_stats() -> dict[str, Any]:
    """清除看板：完成面积等指标实时按明细重算，刷新后即为最新值。"""
    return service.stats()


@router.get("/review", response_model=PageResult[dict])
def review_queue() -> PageResult[dict]:
    """待复核清单：已登记清除结果、等待复核确认的任务。"""
    items = service.review_queue()
    return PageResult(items=items, total=len(items), page=1, size=len(items) or 1)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出杂草清除清单：返回当前全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "weed", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条除草任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"除草任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记除草任务：结果字段全部落库；同区域未结案任务不重复排单。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="除草任务已登记", entry=entry)


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改除草明细（如补录覆盖程度、订正作业日期）；状态不在此入口变更。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="除草明细已更新", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行安排除草、开始清除、补除登记、补除完成；状态只能单步前进，结果随动作落库。"""
    action = str(payload.values.get("action") or "").strip()
    values = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/review", response_model=ActionResult)
def review_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对待复核任务给出复核结论：通过则结案，不通过则转补除登记。"""
    passed = str(payload.values.get("结论") or "通过").strip() not in ("不通过", "驳回", "fail", "false")
    entry, message = service.review_entry(entry_id, passed)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
