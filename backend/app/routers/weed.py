"""杂草清除接口：维护除草任务，覆盖登记、安排、清除、复核、补除全流程。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.weed import STATUS_ORDER, WeedService

router = APIRouter(prefix="/api/weed", tags=["杂草清除"])

service = WeedService()

STATUSES = STATUS_ORDER


@router.get("/stats")
def board_stats() -> dict[str, Any]:
    """清除看板：完成面积等指标每次都按明细重算，不使用任何缓存值。"""
    return service.board_stats()


@router.get("/review")
def review_queue() -> dict[str, Any]:
    """待复核清单：清除结果登记完成的任务都会落到这里。"""
    items = service.review_queue()
    return {"module": "weed", "total": len(items), "items": items}


@router.post("/auto-schedule", response_model=ActionResult)
def auto_schedule() -> ActionResult:
    """把待安排任务自动排进作业单；同一区域只生效一次，已清完的不再排入。"""
    result = service.auto_schedule()
    message = f"已安排 {result['scheduled_count']} 条"
    if result["skipped_count"]:
        message += f"，跳过 {result['skipped_count']} 条（区域重复或已清除）"
    return ActionResult(ok=True, message=message, entry={"result": result})


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按除草编号检索"),
    status: str | None = Query(default=None, description="待安排、已安排、清除中、待复核、需补除、已清除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按除草编号与状态过滤杂草清除列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出杂草清除清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "weed", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条除草任务明细；与列表页同源，字段不会对不上。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"除草任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条除草任务，缺字段时说明原因而不是静默丢弃。

    同一片区域已有在途除草任务时不重复建单，直接返回原任务。
    """
    entry, missing, created = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if not created:
        return ActionResult(
            ok=True,
            message=f"该区域已有在途除草安排（编号 {entry.get('除草编号')}），重复提交未再生效",
            entry=entry,
        )
    return ActionResult(ok=True, message="除草任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条除草任务执行动作；动作必须与当前状态匹配，状态只能一步步往前走。

    动作支持：安排除草、开始清除、登记清除结果、重新获取结果、
    补除登记、复核驳回、复核通过。业务字段随动作一并提交并落库。
    """
    action = str(payload.values.get("action") or "").strip()
    fields = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message = service.run_action(entry_id, action, fields)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
