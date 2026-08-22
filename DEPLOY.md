# 谷里 GuLane 工作台 · 免费永久部署指南

目标：**前端静态托管（永久地址）+ 后端常驻服务（接收爬虫 POST、存库、每日企微简报）**，
全部用免费方案，地址不失效，可随时迭代。

---

## 一、架构

```
浏览器 ──打开──> 前端工作台 (单文件 HTML，GitHub Pages / Cloudflare Pages 免费托管)
                     │
                     │ 审核通过后「同步后端」调用开放接口
                     ▼
              后端服务 (FastAPI + SQLite，Render 免费层)
                     ├─ POST /api/open/material/add   (X-API-KEY 鉴权, source_url 幂等)
                     ├─ GET  /api/open/material        读取素材
                     └─ 每日 08:00 企微机器人简报推送
```

---

## 二、前端部署（GitHub Pages，永久免费）

1. 在 GitHub 新建仓库，例如 `gulane-workbench`。
2. 把 `谷里GuLane运营管理工作台.html` 重命名为 `index.html` 上传到仓库根目录。
   （本项目根目录已有该文件，直接 `git add` 上传即可。）
3. 仓库 Settings → Pages → Source 选 `main` 分支 `/ root` → Save。
4. 几分钟后访问 `https://<你的用户名>.github.io/gulane-workbench/` 即永久可用。
5. **迭代**：以后改了 build 里的碎片 → 运行 `assemble.py` 重新拼装 → 把新 `index.html` 推上去，
   地址永远不变。

可选：Settings → Pages → Custom domain 绑定自己的域名（如 `workbench.gulane.com`）。

> Cloudflare Pages 替代方案：连 GitHub 仓库，构建命令留空，输出目录设为仓库根，
> 同样免费且更快。

---

## 三、后端部署（Render 免费层）

1. 把 `backend/` 目录（main.py / requirements.txt / render.yaml / Procfile）推到另一个 GitHub 仓库，
   或同一仓库的 `backend/` 子目录（Render 指定 root 为该目录）。
2. 打开 https://render.com → New → Web Service → 连 GitHub 仓库。
3. Render 会自动读 `render.yaml`：runtime=python、plan=free、start=uvicorn。
4. 在 Render 的 Environment 里设置三个变量（和前端 INTEG 对应）：
   - `X_API_KEY`：你自己定的一串密钥，例如 `gulane_xxx_2026`（**同时填到前端「素材库 X-API-KEY」**）
   - `WECOM_WEBHOOK`：企业微信机器人 webhook 完整 URL（填了才推送简报）
   - `FRONTEND_URL`：你的前端地址，如 `https://<用户名>.github.io/gulane-workbench`
5. Deploy。完成后得到固定地址 `https://gulane-material-api.onrender.com`（可在 Render 绑自定义子域）。
6. **迭代**：改 `main.py` 推上去，Render 自动重新部署，地址不变。

> Railway 替代：连仓库 → 选 Python → 同样设三个环境变量，免费额度够个人用。

---

## 四、前后端对接（在你工作台里填）

进入工作台 **设置 → 集成**，填：

| 字段 | 填什么 |
|---|---|
| 素材库接口域名（API_BASE） | 后端地址，如 `https://gulane-material-api.onrender.com` |
| 素材库接口路径（MATERIAL_ADD_PATH） | 默认 `/api/open/material/add`，无需改 |
| 素材库 X-API-KEY | 与后端 `X_API_KEY` 完全一致 |
| 企业微信 Webhook（每日简报） | 机器人 webhook（也可只填在后端 `WECOM_WEBHOOK`，二选一生效） |

填完保存 → 在前端「内容素材库」审核素材 → 点「同步后端」即真实写入数据库。
每日 08:00（北京时）后端自动汇总推送企微。

---

## 五、本地调试

```bash
cd backend
pip install -r requirements.txt
# 可选：设环境变量
export X_API_KEY=gulane_local_test
export WECOM_WEBHOOK=
export FRONTEND_URL=http://localhost:8000
python main.py
# 健康检查
curl http://localhost:8000/health
# 手动触发简报
curl http://localhost:8000/api/open/digest/run
```

测试写入一条素材：
```bash
curl -X POST http://localhost:8000/api/open/material/add \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: gulane_local_test" \
  -d '{"category_code":"cafe","title_zh":"测试联名咖啡","source_url":"https://example.com/post/1","ai_group_copy":"社群文案"}'
```

---

## 六、费用与失效说明

- GitHub Pages / Cloudflare Pages：**永久免费**，不活跃也不会删。
- Render free：`web` 服务 750 小时/月（个人够用），闲置 15 分钟会休眠，下次访问冷启动约 1-2 秒。
  地址**永久不变**。
- SQLite 文件随服务存储，免费层重启可能重置磁盘 → 如需持久，Render 挂 Disk 或用 Cloudflare D1 / 升级付费。
  个人素材量小、且前端 localStorage 也有副本，风险可控。

> 重要：前端数据存在**各浏览器 localStorage**，换设备需重新填或导出导入。
> 后端数据库是「对外接收爬虫 + 汇总推送」的权威存储；两者通过开放接口同步。
