# 小剧场排练 · 剧本换版网页

给小剧场排练用的剧本工作稿 / 换版工具。Python Flask + SQLite + 原生 JavaScript，无任何前端框架。

## 一条命令启动

```bash
docker compose up --build
```

启动后在浏览器打开：

> **http://localhost:8000**

停止：`docker compose down`（数据保留在 Docker 命名卷里；加 `-v` 才会连数据一起删除）。

- 首次启动会自动建库并写入一小段示例工作稿（3 个角色、9 句台词，第 5 句带一条示例笔记）。
- 数据库文件是容器卷 `/data/theater.db`，容器重启、`docker compose restart`、主机重启后，**工作稿和所有发布版都保留**。

## 页面说明

| 页面 | 地址 | 说明 |
| --- | --- | --- |
| 首页 | `/` | 工作稿概览、角色页入口、历史发布版列表 |
| 角色页 | `/character/<角色名>` | 只看本角色台词 + 每句**紧前一句**的提示，可给自己的台词写笔记 |
| 编辑器 | `/editor` | 增删、改派、改词、重排台词，发布新版本 |
| 发布版总览 | `/r/<修订号>/` | 某个已冻结版本的角色入口 |
| 发布版角色页 | `/r/<修订号>/character/<角色名>` | 冻结的角色页和笔记，只读 |

## 规则是怎么落实的

### 台词编号永不复用

`lines.id` 使用 SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`，删除编号不会被新台词重新占用；
台词顺序由独立的 `position`（1..n 密集序号）维护，重排只改 `position`，编号不动。

### 提示随顺序重算

角色页不存“提示”，每次读取时按当前 `position` 把**全稿紧前一句**（任何角色的前一句）附在每句台词上。
所以编辑器里一重排，所有角色页的提示立即按新顺序变化。

### 笔记始终跟随原台词编号

笔记表 `notes` 以 `line_id` 为键，重排（只动 `position`）和改词都不碰笔记。
删除或**改派**仍带笔记的台词时，服务器整次拒绝（HTTP 409），
响应里列出必须先处理的笔记（编号、原角色、内容摘要）和角色页链接；
只有演员到自己的角色页清空（保存空白）笔记后，操作才能成功。
笔记因此不可能落到别的台词或别的角色名下。

### 乐观锁：编辑必须带所见修订号

工作稿有一个单调递增的修订号 `working_revision`，从 1 开始。

- 编辑器里的**增删、改派、改词、重排**，角色页里的**保存/清空笔记**，请求都必须带 `base_revision`。
- 服务器在 `BEGIN IMMEDIATE` 写事务里比对：不匹配就回滚并返回 409 `stale_revision`，**整次拒绝，一个字都不会写进去**，也不会覆盖别人（别的页面）的改动。
- 任何一次成功的写操作都在同一事务内把修订号 +1。前端收到 409 后自动重新载入最新稿。
- 内容没有实际变化的提交不改修订号。

### 发布：所有角色页和笔记冻结在同一修订

编辑器点「发布当前工作稿」时，在一个事务里把当前全部台词和笔记复制到
`published_lines` / `published_notes`，与发布时的工作稿修订号绑定（`/r/<修订>/...`）。

- 发布**不增加工作稿修订号**、不动工作稿，之后继续改工作稿完全不影响旧版。
- 同一个修订号只能发布一次，重复发布会被拒绝；再改一笔（修订号前进）后才能发布下一版。
- 历史版本永久可打开，冻结页里的笔记只读。

## 主要 HTTP/JSON 接口

所有写接口都要在 JSON 请求体里带 `base_revision`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/draft` | 完整工作稿（含全部笔记、提示、修订号） |
| GET | `/api/character/<角色名>` | 工作稿中某角色的角色页数据 |
| GET | `/api/revisions/<修订>/character/<角色名>` | 发布版角色页数据 |
| GET | `/api/publications` | 发布版列表 |
| POST | `/api/lines` | 新增（`character`、`text`、可选 `after_id`、`base_revision`） |
| PATCH | `/api/lines/<id>` | 改派 / 改词（有笔记时改派被拒） |
| DELETE | `/api/lines/<id>` | 删除（有笔记被拒） |
| POST | `/api/lines/reorder` | 按编号数组重排（必须恰好包含全部台词） |
| PUT | `/api/lines/<id>/note` | 保存笔记（`content` 为空即清空） |
| POST | `/api/publish` | 冻结发布当前工作稿 |

冲突响应示例：

```json
{
  "error": "修订号已过期：你看到的是第 3 版，工作稿现在是第 4 版……",
  "code": "stale_revision",
  "details": { "submitted": 3, "current": 4 }
}
```

笔记阻挡响应的 `details` 形如：

```json
[{ "line_id": 5, "character": "林夏", "content": "数三拍时看向窗外的灯……" }]
```

## 本地直接运行（不用 Docker）

需要 Python 3.10+：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
flask --app app init-db
python app.py            # 或：gunicorn -b 0.0.0.0:8000 app:app
```

默认数据库在项目下的 `data/theater.db`（可用环境变量 `DB_PATH` 修改），同样在 http://localhost:8000 访问。

## 目录结构

```
app.py                 # Flask 应用、SQLite 表结构与全部 API
templates/             # base / 首页 / 角色页 / 编辑器 / 发布版页 / 404
static/                # style.css、common.js、character.js、editor.js
Dockerfile             # init-db + gunicorn
docker-compose.yml     # 单命令启动，命名卷 theater-data 持久化 /data
requirements.txt
```
