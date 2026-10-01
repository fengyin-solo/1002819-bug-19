"""杂草清除业务规则测试：覆盖字段落库、状态门禁、幂等安排、待复核与看板口径。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.weed import WeedService, store  # noqa: E402

client = TestClient(app)


def reset_weed_rows() -> None:
    """每个用例用一块干净的、字段完整的作业数据。"""
    store._tables["weed"] = [
        {
            "id": 1,
            "status": "待安排",
            "pending": True,
            "abnormal": False,
            "除草编号": "WEED-T001",
            "除草区域": "一号绿地",
            "杂草种类": "狗尾草",
            "覆盖程度": "约30%",
            "作业面积": 10.0,
        },
        {
            "id": 2,
            "status": "已清除",
            "pending": False,
            "abnormal": False,
            "除草编号": "WEED-T002",
            "除草区域": "二号绿地",
            "杂草种类": "香附子",
            "覆盖程度": "约2%",
            "作业面积": 5.5,
        },
    ]


def action(entry_id: int, action: str, **values):
    payload = {"action": action, **values}
    res = client.post(f"/api/weed/{entry_id}/actions", json={"values": payload})
    return res.status_code, res.json()


def test_create_persists_all_fields_not_only_required():
    """登记时覆盖程度、面积等字段不能整段丢失。"""
    reset_weed_rows()
    res = client.post("/api/weed", json={"values": {
        "除草编号": "WEED-T003",
        "除草区域": "三号绿地",
        "杂草种类": "牛筋草",
        "覆盖程度": "约40%",
        "作业面积": "12.5",
        "除草方式": "人工拔除",
    }})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    entry = body["entry"]
    assert entry["覆盖程度"] == "约40%"
    assert entry["作业面积"] == "12.5"
    assert entry["除草方式"] == "人工拔除"
    # 详情页和列表页同源，字段必须对得上
    detail = client.get(f"/api/weed/{entry['id']}").json()
    listed = client.get("/api/weed").json()["items"]
    listed_row = next(row for row in listed if row["id"] == entry["id"])
    for view in (detail, listed_row):
        assert view["杂草种类"] == "牛筋草"
        assert view["覆盖程度"] == "约40%"
        assert view["除草状态"] == "待安排"
        assert view["status"] == "待安排"


def test_clearing_result_updates_coverage_and_enters_review_queue():
    """清完之后覆盖程度必须改，并落到待复核清单。"""
    reset_weed_rows()
    status_code, body = action(
        1, "安排除草",
    )
    assert body["ok"] is True
    status_code, body = action(1, "开始清除", 除草方式="机械修剪")
    assert body["ok"] is True
    # 清完登记：覆盖率从 30% 降到 5%
    status_code, body = action(
        1, "登记清除结果", 覆盖程度="约5%", 作业面积="10", 除草方式="机械修剪",
        作业日期="2026-10-01", 作业人员="王强",
    )
    assert body["ok"] is True, body["message"]
    assert body["entry"]["覆盖程度"] == "约5%"
    assert body["entry"]["status"] == "待复核"
    assert body["entry"]["除草状态"] == "待复核"
    assert body["entry"]["pending"] is True

    queue = client.get("/api/weed/review").json()
    ids = [item["id"] for item in queue["items"]]
    assert 1 in ids
    queued = next(item for item in queue["items"] if item["id"] == 1)
    assert queued["覆盖程度"] == "约5%"


def test_status_can_only_move_forward_step_by_step():
    """不许后退：已安排不能退回待安排；也不许跳步。"""
    reset_weed_rows()
    code, body = action(1, "安排除草")
    assert body["ok"] is True
    assert body["entry"]["status"] == "已安排"
    # 已安排再点"安排除草"（等价于退回待安排）必须被拦
    code, body = action(1, "安排除草")
    assert body["ok"] is False
    assert "只能在" in body["message"]
    # 待安排不能直接登记清除结果（跳步）
    code, body = action(2, "复核通过")
    assert body["ok"] is False


def test_reject_then_resow_returns_to_review_with_new_coverage():
    """复核驳回 -> 补除登记（覆盖率更新）-> 复核通过。"""
    reset_weed_rows()
    action(1, "安排除草")
    action(1, "开始清除", 除草方式="人工拔除")
    _, body = action(1, "登记清除结果", 覆盖程度="约15%", 作业面积="10")
    assert body["entry"]["status"] == "待复核"
    _, body = action(1, "复核驳回")
    assert body["entry"]["status"] == "需补除"
    assert body["entry"]["abnormal"] is True
    # 补除后覆盖率必须更新，不能还是老数
    _, body = action(1, "补除登记", 覆盖程度="约4%", 作业面积="10", 除草方式="人工拔除")
    assert body["ok"] is True
    assert body["entry"]["覆盖程度"] == "约4%"
    assert body["entry"]["status"] == "待复核"
    _, body = action(1, "复核通过")
    assert body["entry"]["status"] == "已清除"
    assert body["entry"]["pending"] is False


def test_result_unavailable_can_be_retried_without_state_change():
    """取不到结果（缺覆盖程度/面积）时状态不动，可以重新取一次接着走。"""
    reset_weed_rows()
    action(1, "安排除草")
    action(1, "开始清除", 除草方式="机械修剪")
    _, body = action(1, "登记清除结果", 覆盖程度="", 作业面积="")
    assert body["ok"] is False
    assert "清除结果缺少" in body["message"]
    # 状态仍是清除中，可以重新提交
    entry = client.get("/api/weed/1").json()
    assert entry["status"] == "清除中"
    _, body = action(1, "重新获取结果", 覆盖程度="约6%", 作业面积="10")
    assert body["ok"] is True
    assert body["entry"]["status"] == "待复核"
    assert body["entry"]["覆盖程度"] == "约6%"


def test_same_area_schedule_is_idempotent():
    """同一片区域重复提交安排除草只生效一次。"""
    reset_weed_rows()
    first = client.post("/api/weed", json={"values": {
        "除草编号": "WEED-T010", "除草区域": "重复区域", "杂草种类": "狗尾草",
        "覆盖程度": "约20%", "作业面积": "3",
    }})
    new_id = first.json()["entry"]["id"]
    # 再次登记同一区域：不建新单
    second = client.post("/api/weed", json={"values": {
        "除草编号": "WEED-T011", "除草区域": "重复区域", "杂草种类": "狗尾草",
    }})
    assert second.json()["entry"]["id"] == new_id
    assert len(client.get("/api/weed").json()["items"]) == 3  # 初始两条 + 新增一条

    action(new_id, "安排除草")
    # 同区域再来一张待安排单，对其执行安排也不重复生效
    other = client.post("/api/weed", json={"values": {
        "除草编号": "WEED-T012", "除草区域": "别的区域", "杂草种类": "三叶草",
    }})
    other_id = other.json()["entry"]["id"]
    action(other_id, "安排除草")
    # 直接对已存在的在途区域建单被幂等拦截
    third = client.post("/api/weed", json={"values": {
        "除草编号": "WEED-T013", "除草区域": "别的区域", "杂草种类": "三叶草",
    }})
    assert third.json()["entry"]["id"] == other_id


def test_auto_schedule_skips_cleared_area_and_duplicates():
    """自动排程：同一区域只排一次，已经清完的不再排进去。"""
    reset_weed_rows()
    # 一号绿地待安排，且该区域之后已有"已清除"记录时应跳过——先造数据
    store._tables["weed"].append({
        "id": 3, "status": "已清除", "pending": False, "abnormal": False,
        "除草编号": "WEED-T003", "除草区域": "一号绿地", "杂草种类": "狗尾草",
        "覆盖程度": "约2%", "作业面积": 10.0,
    })
    # 四号绿地待安排，可以正常排
    store._tables["weed"].append({
        "id": 4, "status": "待安排", "pending": True, "abnormal": False,
        "除草编号": "WEED-T004", "除草区域": "四号绿地", "杂草种类": "葎草",
        "覆盖程度": "约22%", "作业面积": 7.0,
    })
    res = client.post("/api/weed/auto-schedule", json={}).json()
    scheduled_ids = [item["id"] for item in res["entry"]["result"]["scheduled"]]
    skipped_ids = [item["id"] for item in res["entry"]["result"]["skipped"]]
    assert 4 in scheduled_ids
    assert 1 in skipped_ids
    # 二号绿地本来就是已清除，不在处理范围
    statuses = {row["id"]: row["status"] for row in client.get("/api/weed").json()["items"]}
    assert statuses[1] == "待安排"
    assert statuses[4] == "已安排"
    # 再排一次：没有待安排被重复处理
    again = client.post("/api/weed/auto-schedule", json={}).json()
    assert again["entry"]["result"]["scheduled_count"] == 0


def test_board_done_area_recomputed_from_details():
    """看板完成面积始终按明细重算：复核通过后增加，数据刷新不丢失。"""
    reset_weed_rows()
    stats = client.get("/api/weed/stats").json()
    assert stats["完成面积"] == 5.5
    assert stats["已清除"] == 1

    action(1, "安排除草")
    action(1, "开始清除", 除草方式="机械修剪")
    action(1, "登记清除结果", 覆盖程度="约5%", 作业面积="10")
    # 待复核期间不计入完成面积，计入待复核面积
    stats = client.get("/api/weed/stats").json()
    assert stats["完成面积"] == 5.5
    assert stats["待复核面积"] == 10.0
    action(1, "复核通过")
    stats = client.get("/api/weed/stats").json()
    assert stats["完成面积"] == 15.5
    assert stats["已清除"] == 2

    # 用一个全新 service 实例（模拟"刷新后"）重新算，结果必须一致
    fresh_stats = WeedService().board_stats()
    assert fresh_stats["完成面积"] == 15.5


def test_work_date_and_method_change_together_on_finish():
    """作业日期动过，除草方式也要跟着登记后的结果走（不再是老数）。"""
    reset_weed_rows()
    action(1, "安排除草")
    action(1, "开始清除", 除草方式="人工拔除")
    _, body = action(
        1, "登记清除结果", 覆盖程度="约5%", 作业面积="10",
        除草方式="化学除草", 作业日期="2026-10-01", 作业人员="李敏",
    )
    entry = body["entry"]
    assert entry["作业日期"] == "2026-10-01"
    assert entry["除草方式"] == "化学除草"
    assert entry["作业人员"] == "李敏"
