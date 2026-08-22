# -*- coding: utf-8 -*-
"""
谷里 GuLane · 联动资讯素材库 开放接口后端
=================================================
对齐《WorkBuddy 联动资讯素材库 后端开发对接文档 V1.0》

功能：
  1. POST /api/open/material/add   接收素材入库（X-API-KEY 鉴权，source_url 幂等）
  2. GET  /api/open/material       分页读取素材（X-API-KEY 鉴权）
  3. GET  /material/detail/{id}    素材详情页（前端托管域名下的路由，后端返回跳转信息）
  4. GET  /health                 健康检查
  5. 每日 08:00（北京时）自动汇总当日新增，推送企业微信机器人简报

存储：SQLite（文件 gulane.db），零外部依赖、免费层可跑。
部署：Render / Railway 免费层（见 render.yaml / Procfile）。
"""
import os
import sqlite3
import json
import threading
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI, Request, Header, HTTPException, Query, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List

# ============================================================
# 配置
# ============================================================
X_API_KEY = os.environ.get("X_API_KEY", "")          # 后端分配给爬虫端的固定密钥
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")  # 企业微信机器人 webhook
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")    # 前端工作台地址（用于拼接 detail_url）
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "gulane.db"))
# 持久化目录保障：挂载磁盘（如 Render /data）时目录可能尚未存在，sqlite 不会自动建目录
_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    os.makedirs(_db_dir, exist_ok=True)

# 文档 5 分类枚举（严格对齐）
CAT_MAP = {
    "cafe": "联动咖啡馆",
    "goods": "周边商品",
    "popup": "快闪店",
    "prize": "抽奖一番赏",
    "event": "线下活动",
}
CAT_CODES = list(CAT_MAP.keys())

# 北京时区（UTC+8）
CN_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="谷里GuLane 联动资讯素材库开放接口", version="1.0.0")

# 允许 GitHub Pages / 本地开发等跨域来源访问开放接口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 数据库
# ============================================================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS material (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_code TEXT NOT NULL,
            category_name TEXT,
            title_ja TEXT DEFAULT '',
            title_zh TEXT DEFAULT '',
            content_ja TEXT DEFAULT '',
            content_zh TEXT DEFAULT '',
            cover_image TEXT DEFAULT '',
            images TEXT DEFAULT '',
            goods_count INTEGER DEFAULT 0,
            source_url TEXT UNIQUE,
            publish_date TEXT DEFAULT '',
            activity_start_date TEXT DEFAULT '',
            activity_end_date TEXT DEFAULT '',
            address TEXT DEFAULT '',
            order_url TEXT DEFAULT '',
            ai_group_copy TEXT DEFAULT '',
            ai_xiaohongshu_copy TEXT DEFAULT '',
            ai_moments_copy TEXT DEFAULT '',
            review_status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    # 迁移：旧表新增字段（兼容已部署实例）
    try:
        conn.execute("ALTER TABLE material ADD COLUMN address TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE material ADD COLUMN order_url TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()


init_db()


def now_cn():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_cn():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


# ============================================================
# 鉴权
# ============================================================
def require_key(x_api_key: Optional[str] = Header(None)):
    if not X_API_KEY:
        # 未配置密钥时放行（开发/演示），生产务必配置
        return
    if x_api_key != X_API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-KEY")


def ok(data=None, message="success"):
    return JSONResponse({"code": 0, "message": message, "data": data or {}})


def fail(code, message, status=400):
    return JSONResponse({"code": code, "message": message, "data": {}}, status_code=status)


# ============================================================
# 请求体（严格对齐文档 3.3）
# ============================================================
class MaterialAdd(BaseModel):
    category_code: str
    title_ja: Optional[str] = ""
    title_zh: Optional[str] = ""
    content_ja: Optional[str] = ""
    content_zh: Optional[str] = ""
    cover_image: Optional[str] = ""
    images: Optional[str] = ""
    goods_count: Optional[int] = 0
    source_url: str
    publish_date: Optional[str] = ""
    activity_start_date: Optional[str] = ""
    activity_end_date: Optional[str] = ""
    address: Optional[str] = ""
    order_url: Optional[str] = ""
    ai_group_copy: Optional[str] = ""
    ai_xiaohongshu_copy: Optional[str] = ""
    ai_moments_copy: Optional[str] = ""


# ============================================================
# 接口
# ============================================================
@app.get("/")
def root():
    return {"code": 0, "message": "gulane-material-api is running", "data": {"docs": "/docs"}}


@app.get("/health")
def health():
    return {"code": 0, "message": "ok", "data": {"time": now_cn()}}


# 临时管理接口：清空素材库（部署期清理脏数据用）
@app.post("/_admin/clear-materials")
def admin_clear_materials(_=Depends(require_key)):
    conn = get_conn()
    conn.execute("DELETE FROM material")
    conn.commit()
    conn.close()
    return ok({"cleared": True}, message="素材库已清空")


@app.post("/api/open/material/add")
def material_add(payload: MaterialAdd, _=Depends(require_key)):
    # 分类枚举校验
    if payload.category_code not in CAT_CODES:
        return fail(400, "category_code 必须是 %s 之一" % "/".join(CAT_CODES))
    if not payload.source_url:
        return fail(400, "source_url 不能为空（作为幂等键）")

    conn = get_conn()
    # 幂等：source_url 唯一（文档 5.2）
    exist = conn.execute(
        "SELECT id FROM material WHERE source_url=?", (payload.source_url,)
    ).fetchone()
    if exist:
        conn.close()
        mid = exist["id"]
        return ok(
            {"material_id": mid, "detail_url": detail_url(mid), "duplicate": True},
            message="source_url 已存在，未重复入库",
        )

    cat_name = CAT_MAP.get(payload.category_code, "")
    cur = conn.execute(
        """
        INSERT INTO material (
            category_code, category_name, title_ja, title_zh, content_ja, content_zh,
            cover_image, images, goods_count, source_url, publish_date,
            activity_start_date, activity_end_date, address, order_url, ai_group_copy,
            ai_xiaohongshu_copy, ai_moments_copy, review_status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.category_code, cat_name, payload.title_ja, payload.title_zh,
            payload.content_ja, payload.content_zh, payload.cover_image, payload.images,
            payload.goods_count or 0, payload.source_url, payload.publish_date,
            payload.activity_start_date, payload.activity_end_date,
            payload.address or "", payload.order_url or "",
            payload.ai_group_copy, payload.ai_xiaohongshu_copy, payload.ai_moments_copy,
            "pending", now_cn(), now_cn(),
        ),
    )
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    return ok({"material_id": mid, "detail_url": detail_url(mid)}, message="入库成功")


@app.get("/api/open/material")
def material_list(
    _=Depends(require_key),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    conn = get_conn()
    where = "WHERE 1=1"
    args = []
    if category:
        if category not in CAT_CODES:
            conn.close()
            return fail(400, "category 必须是 %s 之一" % "/".join(CAT_CODES))
        where += " AND category_code=?"
        args.append(category)
    total = conn.execute("SELECT COUNT(*) c FROM material " + where, args).fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM material " + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size],
    ).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    return ok({"total": total, "page": page, "size": size, "items": items})


@app.post("/api/open/material/cleanup")
def material_cleanup(
    _=Depends(require_key),
    cutoff: Optional[str] = Query(None, description="过期判定日，格式 YYYY-MM-DD，默认今天（活动结束日 < 该日期即清理）"),
):
    """活动过期自动清理：删除 activity_end_date 非空且早于 cutoff 的素材。

    设计原则（与用户约定一致）：
    - 只有「活动结束日已过期」的才清理；没有活动结束日的纯文章/周边长期保留。
    - cutoff 默认今天；爬虫侧调用时可传统一时间，保证前后端判定一致。
    - 满足「所有数据先保存，过期才清理」：入库不丢，仅过期删除。
    """
    if not cutoff:
        cutoff = today_cn()
    # 简单校验格式
    try:
        datetime.strptime(cutoff, "%Y-%m-%d")
    except ValueError:
        return fail(400, "cutoff 格式应为 YYYY-MM-DD")
    conn = get_conn()
    # 仅清理有活动结束日且结束日 < cutoff 的记录
    cur = conn.execute(
        "DELETE FROM material WHERE activity_end_date IS NOT NULL "
        "AND activity_end_date <> '' AND activity_end_date < ?",
        (cutoff,),
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return ok({"deleted": deleted, "cutoff": cutoff, "message": "已清理 %d 条过期活动素材" % deleted})


def detail_url(mid):
    base = FRONTEND_URL.rstrip("/") if FRONTEND_URL else ""
    # 文档 5.3 路由 /material/detail/{id}
    return "%s/material/detail/%s" % (base, mid) if base else "/material/detail/%s" % mid


@app.get("/material/detail/{mid}", response_class=HTMLResponse)
def material_detail(mid: int):
    """详情页：前端是单文件 HTML（路由在前端），后端返回跳转信息页。
    若 FRONTEND_URL 已配置，可直接返回前端页面（前端按 hash/路由渲染详情）。"""
    if FRONTEND_URL:
        # 单文件工作台自行处理 /material/detail/{id} 路由
        return HTMLResponse(
            '<!doctype html><meta http-equiv="refresh" content="0;url=%s#/material/detail/%s">'
            % (FRONTEND_URL, mid)
        )
    conn = get_conn()
    row = conn.execute("SELECT * FROM material WHERE id=?", (mid,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("<h1>素材不存在</h1>", status_code=404)
    r = dict(row)
    html = "<h2>%s</h2><p>分类：%s</p><p>%s</p>" % (
        r.get("title_zh") or r.get("title_ja"),
        CAT_MAP.get(r.get("category_code"), ""),
        r.get("content_zh") or r.get("content_ja") or "",
    )
    return HTMLResponse(html)


# ============================================================
# 每日 08:00 简报（文档 5.4）
# ============================================================
def build_daily_digest():
    today = today_cn()
    conn = get_conn()
    rows = conn.execute(
        "SELECT category_code, COUNT(*) c FROM material WHERE created_at LIKE ? GROUP BY category_code",
        (today + "%",),
    ).fetchall()
    conn.close()
    if not rows:
        return None
    total = sum(r["c"] for r in rows)
    lines = [
        "📣 **联动资讯素材库 · 每日汇总** (%s)" % today,
        "新增素材：%d 条" % total,
    ]
    for r in rows:
        lines.append("· %s：%d 条" % (CAT_MAP.get(r["category_code"], r["category_code"]), r["c"]))
    lines.append("详情前往工作台「内容素材库」")
    return "\n".join(lines)


def push_wecom(markdown_text):
    if not WECOM_WEBHOOK:
        print("[wecom] 未配置 WECOM_WEBHOOK，跳过推送")
        return False
    try:
        resp = requests.post(
            WECOM_WEBHOOK,
            json={"msgtype": "markdown", "markdown": {"content": markdown_text}},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print("[wecom] 推送失败:", e)
        return False


def daily_job():
    md = build_daily_digest()
    if md:
        push_wecom(md)
        print("[daily] 简报已推送")
    else:
        print("[daily] 今日无新增，跳过")


def _scheduler():
    """极简调度：每 60s 检查一次是否到 08:00（北京时），到点执行一次。"""
    last_run = ""
    while True:
        now = datetime.now(CN_TZ)
        if now.hour == 8 and now.minute == 0 and now.strftime("%Y-%m-%d %H:%M") != last_run:
            last_run = now.strftime("%Y-%m-%d %H:%M")
            try:
                daily_job()
            except Exception as e:
                print("[daily] error:", e)
        import time
        time.sleep(30)


@app.on_event("startup")
def start_scheduler():
    if os.environ.get("ENABLE_SCHEDULER", "true").lower() == "false":
        return
    t = threading.Thread(target=_scheduler, daemon=True)
    t.start()


# ============================================================
# 本地手动触发简报（调试用）：GET /api/open/digest/run
# ============================================================
@app.get("/api/open/digest/run")
def run_digest():
    daily_job()
    return ok(message="digest dispatched")


# redeploy trigger: V3 detail page full fields (address/order_url) — 2026-08-23
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
