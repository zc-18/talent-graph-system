# 智岗图谱 · TalentGraph AI

> **多源异构数据驱动岗位和能力图谱构建与动态演化分析系统**
> 挑战杯 揭榜挂帅 · 题目编号 XH-202621 · 发榜单位：科大讯飞股份有限公司

🌐 **在线系统**：http://101.200.184.201:8200/

以"数据驱动 + 大模型 + 知识图谱"为核心，构建可自我进化的"人才能力大脑"，实现 **多源数据采集 → 新岗位发现与定义 / 既有岗位能力更新 → 能力图谱动态构建 → 简历解析 → 精准匹配与差距分析 → 改进建议与学习路径** 的全流程闭环。

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 🔍 **新岗位发现与定义** | 多源联网检索（Tavily + Serper）+ RAG 接地，识别萌芽中的新兴岗位并生成结构化定义 |
| 🔀 **既有岗位能力动态演化** | 用最新 JD 驱动既有岗位更新，自动标注新增/删除/修改并溯源 |
| 🕸️ **全景能力图谱** | 技能点级粒度力导向图，按技术栈/级别/置信度切换视图，可下钻 |
| 🎯 **人岗匹配诊断** | 简历解析（PDF/Word）→ 多维匹配 → 差距分析 → 学习路径 + 改进建议 |

## 🚀 技术亮点（创新点）

- **多源异构数据清洗与交叉验证**：SimHash 去抄袭、通胀检测、时滞降权、同义词归一化降噪；
- **能力"幻觉"防控**：≥2 独立来源交叉验证 + 置信度加权 + 证据溯源，低置信能力项自动过滤；
- **嵌入模型退化守卫**：针对中文向量模型对英文 token 退化的工程化防控，匹配准确率 86.9%→100%；
- **RAG 接地新岗位发现**：先检索真实证据再生成，从源头降低大模型幻觉。

## 📊 核心指标（全部超过 90% 达标线）

| JD 解析 F1 | 简历提取 F1 | 人岗匹配准确率 | 测试覆盖率 | 测试 JD |
|:---:|:---:|:---:|:---:|:---:|
| **98.6%** | **96.5%** | **100%** | **64%** | **303 条** |

---

## 🏗️ 技术栈

- **后端**：FastAPI · SQLAlchemy 2.0 · Pydantic v2 · DeepSeek（大模型）· BGE（向量）· Tavily + Serper（检索）
- **前端**：React 18 · TypeScript · Vite · TailwindCSS · Framer Motion · ECharts
- **数据库**：MySQL（utf8mb4）
- **部署**：systemd + uvicorn（单 worker，同端口托管前端）/ Docker

## 📁 目录结构

```
talent-graph-system/
├── backend/
│   ├── app/                FastAPI 应用（services 分层 + routers）
│   ├── data/               数据生成 / pipeline / 评测脚本
│   ├── tests/              单元测试（44 用例，覆盖率 64%）
│   └── requirements.txt
├── frontend/               React + Vite 前端
├── deploy/                 Dockerfile / docker-compose / systemd
└── docs/                   设计方案 / 测试报告 / 部署说明 / 测试数据 / 截图
```

## ⚡ 快速开始

```bash
# 后端
cd backend
uv sync
uv run python data/run_pipeline.py --reset    # 构建图谱
uv run python data/seed_relations.py
uv run uvicorn app.main:app --port 8200

# 前端
cd frontend
npm install && npm run dev     # 开发  (代理 /api → 127.0.0.1:8200)
npm run build                  # 生产构建

# 测试
cd backend
uv run pytest --cov=app
uv run python data/evaluate.py all
```

## 📚 文档

- [作品设计与实现方案](docs/作品设计与实现方案.md)
- [测试方案与报告](docs/测试方案与报告.md)
- [部署说明](docs/部署说明.md)
- [演示脚本](docs/演示视频脚本.md)
- [测试数据（1 新岗位 + 1 既有岗位 I/O 示例）](docs/测试数据/)
