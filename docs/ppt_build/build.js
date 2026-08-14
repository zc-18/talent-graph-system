// 生成《智岗图谱》作品介绍 PPT（浅蓝清新风，匹配系统 UI）
const PptxGenJS = require('pptxgenjs')
const p = new PptxGenJS()
p.defineLayout({ name: 'W', width: 13.33, height: 7.5 })
p.layout = 'W'
p.author = 'TalentGraph AI'
p.title = '智岗图谱 作品介绍'

// ---- 设计令牌 ----
const F = 'Microsoft YaHei'
const INK = '1E293B', MUTED = '64748B', FAINT = '94A3B8'
const INDIGO = '6366F1', CYAN = '0EA5E9', VIOLET = 'A855F7', EMERALD = '10B981', ROSE = 'F43F5E', AMBER = 'F59E0B'
const CARD = 'FFFFFF', PAGEBG = 'EEF4FF', LINE = 'E2E8F0'
const W = 13.33, H = 7.5

const shadow = { type: 'outer', color: '93A8D8', blur: 12, offset: 3, angle: 90, opacity: 0.28 }

function base(slide, { bg = PAGEBG } = {}) {
  slide.background = { color: bg }
}
let _slideNo = 0
// 顶部页眉：编号 + 标题 + 副标题（编号自动递增，忽略传入值）
function header(slide, num, title, sub) {
  num = String(++_slideNo).padStart(2, '0')
  slide.addShape(p.ShapeType.roundRect, { x: 0.55, y: 0.45, w: 0.62, h: 0.62, rectRadius: 0.12,
    fill: { type: 'solid', color: INDIGO }, line: { type: 'none' }, shadow })
  slide.addText(num, { x: 0.55, y: 0.45, w: 0.62, h: 0.62, align: 'center', valign: 'middle',
    fontFace: F, fontSize: 20, bold: true, color: 'FFFFFF' })
  slide.addText(title, { x: 1.32, y: 0.42, w: 9.5, h: 0.5, fontFace: F, fontSize: 26, bold: true, color: INK })
  if (sub) slide.addText(sub, { x: 1.34, y: 0.95, w: 11, h: 0.32, fontFace: F, fontSize: 12.5, color: MUTED })
  slide.addText('智岗图谱 · TalentGraph AI', { x: 9.6, y: 0.5, w: 3.1, h: 0.3, align: 'right',
    fontFace: F, fontSize: 10, color: FAINT })
}
function card(slide, x, y, w, h, fill = CARD) {
  slide.addShape(p.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.1,
    fill: { type: 'solid', color: fill }, line: { color: LINE, width: 1 }, shadow })
}
function footer(slide, n) {
  slide.addText('http://101.200.184.201:8200', { x: 0.55, y: 7.06, w: 5, h: 0.3, fontFace: F, fontSize: 9, color: FAINT })
  slide.addText(String(_slideNo + 1), { x: 12.4, y: 7.06, w: 0.5, h: 0.3, align: 'right', fontFace: F, fontSize: 9, color: FAINT })
}

// ============ 1. 封面 ============
let s = p.addSlide()
s.background = { path: 'bg.png' }
s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: H, fill: { type: 'solid', color: 'FFFFFF', transparency: 35 }, line: { type: 'none' } })
s.addShape(p.ShapeType.roundRect, { x: 0.9, y: 1.0, w: 0.9, h: 0.9, rectRadius: 0.18, fill: { type: 'solid', color: 'FFFFFF' }, line: { color: 'E2E8F0', width: 1 }, shadow })
s.addImage({ path: 'logo.png', x: 0.96, y: 1.06, w: 0.78, h: 0.78 })
s.addText('智岗图谱', { x: 1.95, y: 0.95, w: 8, h: 0.7, fontFace: F, fontSize: 30, bold: true, color: INK })
s.addText('TalentGraph AI', { x: 1.97, y: 1.62, w: 8, h: 0.4, fontFace: F, fontSize: 14, color: CYAN, bold: true })
s.addText('多源异构数据驱动岗位和能力图谱构建\n与动态演化分析系统', { x: 0.9, y: 2.7, w: 11.5, h: 1.5,
  fontFace: F, fontSize: 38, bold: true, color: INK, lineSpacingMultiple: 1.05 })
s.addText('数据驱动 + 大模型 + 知识图谱 ·  可自我进化的"人才能力大脑"', { x: 0.95, y: 4.35, w: 11, h: 0.5,
  fontFace: F, fontSize: 16, color: MUTED })
const pills = ['新岗位发现', '能力动态演化', '全景能力图谱', '人岗匹配诊断', '反幻觉防控']
pills.forEach((t, i) => {
  s.addShape(p.ShapeType.roundRect, { x: 0.95 + i * 2.16, y: 5.15, w: 2.0, h: 0.5, rectRadius: 0.25,
    fill: { type: 'solid', color: 'FFFFFF' }, line: { color: INDIGO, width: 1 }, shadow })
  s.addText(t, { x: 0.95 + i * 2.16, y: 5.15, w: 2.0, h: 0.5, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, bold: true, color: INDIGO })
})
s.addText('题目编号 XH-202621  |  发榜单位：科大讯飞股份有限公司  |  在线系统：http://101.200.184.201:8200', {
  x: 0.95, y: 6.4, w: 11.5, h: 0.4, fontFace: F, fontSize: 12, color: MUTED })

// ============ 2. 背景与痛点 ============
s = p.addSlide(); base(s); header(s, '01', '背景与痛点', '数字经济时代的人才"结构性矛盾"')
const pains = [
  [ROSE, '企业侧', '招不到合适的人', '新兴岗位识别难、人岗匹配度低，招聘与培养成本居高不下'],
  [AMBER, '人才侧', '看不清职业路径', '新兴领域青年技能需求动态性强，缺乏动态行业技能图谱指引'],
  [VIOLET, '方法侧', '感知不到趋势', '传统关键词匹配无法回答"技术A爆发会给岗位B带来哪些新技能"'],
]
pains.forEach(([c, t, big, desc], i) => {
  const x = 0.6 + i * 4.15
  card(s, x, 1.55, 3.85, 4.0)
  s.addShape(p.ShapeType.roundRect, { x: x + 0.3, y: 1.85, w: 0.9, h: 0.9, rectRadius: 0.16, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(['×', '?', '~'][i], { x: x + 0.3, y: 1.85, w: 0.9, h: 0.9, align: 'center', valign: 'middle', fontFace: F, fontSize: 30, bold: true, color: 'FFFFFF' })
  s.addText(t, { x: x + 0.3, y: 2.95, w: 3.2, h: 0.35, fontFace: F, fontSize: 13, color: MUTED })
  s.addText(big, { x: x + 0.3, y: 3.3, w: 3.3, h: 0.5, fontFace: F, fontSize: 20, bold: true, color: INK })
  s.addText(desc, { x: x + 0.3, y: 3.95, w: 3.3, h: 1.4, fontFace: F, fontSize: 13, color: MUTED, lineSpacingMultiple: 1.25 })
})
s.addText('数据难题：招聘 JD 普遍存在  时滞 · 噪音 · 抄袭 · 通胀；大模型生成能力定义易产生"幻觉"，不可信、不可溯源', {
  x: 0.6, y: 5.85, w: 12.1, h: 0.7, align: 'center', valign: 'middle', fontFace: F, fontSize: 13.5, bold: true, color: INDIGO,
  fill: { type: 'solid', color: 'E0E9FF' }, line: { type: 'none' } })
footer(s, 2)

// ============ 3. 系统总览 全流程闭环 ============
s = p.addSlide(); base(s); header(s, '02', '系统总览 · 全流程闭环', '多源数据采集 → 图谱构建演化 → 匹配诊断的端到端闭环')
const steps = [
  ['多源数据采集', '招聘JD · 联网检索', INDIGO],
  ['清洗·交叉验证', '去抄袭/通胀/时滞', CYAN],
  ['大模型抽取', '结构化能力项', VIOLET],
  ['反幻觉聚合', '置信度 + 溯源', EMERALD],
  ['图谱构建/演化', '动态更新', INDIGO],
  ['匹配与诊断', '差距 + 学习路径', CYAN],
]
steps.forEach(([t, d, c], i) => {
  const x = 0.55 + i * 2.07
  card(s, x, 2.0, 1.85, 1.9)
  s.addText('0' + (i + 1), { x: x, y: 2.15, w: 1.85, h: 0.4, align: 'center', fontFace: F, fontSize: 14, bold: true, color: c })
  s.addText(t, { x: x + 0.08, y: 2.62, w: 1.7, h: 0.6, align: 'center', fontFace: F, fontSize: 13, bold: true, color: INK })
  s.addText(d, { x: x + 0.05, y: 3.2, w: 1.75, h: 0.5, align: 'center', fontFace: F, fontSize: 10, color: MUTED })
  if (i < 5) s.addText('▶', { x: x + 1.78, y: 2.0, w: 0.3, h: 1.9, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, color: FAINT })
})
const kpis = [['32', '岗位（新兴6）'], ['3952', '技能（含3816技能点）'], ['2570', '真实岗位JD'], ['7999', '溯源证据'], ['0.5613', '岗位置信度均值']]
kpis.forEach(([v, l], i) => {
  const x = 0.9 + i * 2.45
  s.addText(v, { x, y: 4.5, w: 2.2, h: 0.7, align: 'center', fontFace: F, fontSize: 32, bold: true, color: INDIGO })
  s.addText(l, { x, y: 5.2, w: 2.2, h: 0.35, align: 'center', fontFace: F, fontSize: 13, color: MUTED })
})
s.addText('技术栈：FastAPI · SQLAlchemy · DeepSeek 大模型 · BGE 向量 · Tavily + Serper 多源检索 · MySQL · React + ECharts', {
  x: 0.6, y: 6.1, w: 12.1, h: 0.6, align: 'center', valign: 'middle', fontFace: F, fontSize: 12.5, color: MUTED,
  fill: { type: 'solid', color: 'FFFFFF' }, line: { color: LINE, width: 1 } })
footer(s, 3)

// ============ 3.5 真实数据采集与合规 ============
s = p.addSlide(); base(s); header(s, '02+', '真实数据采集与合规', '2570 条真实 JD · 官网公开接口合法采集 · 全程台账可溯源')
const srcs = [
  [INDIGO, '腾讯招聘', 'careers.tencent.com 官网公开接口 · 255 条 · 权威度 1.0（批次 2026W31-r1）'],
  [CYAN, '网易招聘', 'hr.163.com 官网公开接口 · 251 条 · 权威度 1.0（批次 2026W31-r1）'],
  [VIOLET, '2018 历史基线', '前程无忧公开数据集 300 条（2018-12 采集）· 权威度 0.7 · 驱动真实 v1→v2 演化'],
  [EMERALD, '权威佐证', '人社部文件 / 头部机构报告 8 条 · 权威度 1.0 · 原文快照归档'],
]
srcs.forEach(([c, t, d], i) => {
  const y = 1.55 + i * 1.02
  card(s, 0.55, y, 7.6, 0.9)
  s.addShape(p.ShapeType.roundRect, { x: 0.8, y: y + 0.18, w: 1.8, h: 0.55, rectRadius: 0.27, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(t, { x: 0.8, y: y + 0.18, w: 1.8, h: 0.55, align: 'center', valign: 'middle', fontFace: F, fontSize: 12.5, bold: true, color: 'FFFFFF' })
  s.addText(d, { x: 2.8, y, w: 5.2, h: 0.9, valign: 'middle', fontFace: F, fontSize: 11.5, color: MUTED, lineSpacingMultiple: 1.1 })
})
card(s, 8.35, 1.55, 4.4, 3.98)
s.addText('代码级采集护栏', { x: 8.6, y: 1.72, w: 3.9, h: 0.4, fontFace: F, fontSize: 14, bold: true, color: INDIGO })
s.addText([
  '· robots.txt 检查 + 单站 ≥4 秒控频',
  '· 不携带登录态，只采公开数据',
  '· PII 双保险：字段拦截 + 正则打码',
  '· 每次请求写入采集台账日志',
  '· jsonl 原始留存 + raw_jd 表溯源',
  '· SimHash 查重检出 25 条重复',
].map(t => ({ text: t, options: { breakLine: true, paraSpaceAfter: 6 } })), { x: 8.6, y: 2.2, w: 3.9, h: 3.2, fontFace: F, fontSize: 11.5, color: MUTED })
s.addText('数据源台账在系统首页可视化：采集 2570 → 查重 → 解析 → 交叉验证 → 入图谱，闭环每一环都有真实数字可查；合成数据集（379 条）重新定位为对抗测试夹具，不进生产图谱', {
  x: 0.55, y: 5.85, w: 12.2, h: 0.85, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, bold: true, color: INDIGO,
  fill: { type: 'solid', color: 'E0E9FF' }, line: { type: 'none' } })
footer(s, 4)

// ============ 4. 全景能力图谱 ============
s = p.addSlide(); base(s); header(s, '03', '新一代信息技术岗位全景图谱', '技能点级粒度 · 可按技术栈/级别/置信度切换视图')
card(s, 0.55, 1.45, 8.1, 5.4)
s.addImage({ path: '02_全景能力图谱.jpeg', x: 0.7, y: 1.6, w: 7.8, h: 5.1, sizing: { type: 'contain', w: 7.8, h: 5.1 } })
const gpts = [
  ['彩色节点 = 岗位', '按技术栈着色，新兴岗位琥珀高亮'],
  ['灰/彩节点 = 技能点', '颗粒度到具体技术，可下钻关联岗位'],
  ['力导向交互', '拖拽 · 缩放 · 邻接聚焦'],
  ['多视图切换', '技术栈 / 级别 / 置信度阈值'],
]
gpts.forEach(([t, d], i) => {
  const y = 1.7 + i * 1.3
  card(s, 8.85, y, 3.9, 1.15)
  s.addShape(p.ShapeType.roundRect, { x: 9.05, y: y + 0.32, w: 0.5, h: 0.5, rectRadius: 0.1, fill: { type: 'solid', color: [INDIGO, CYAN, VIOLET, EMERALD][i] }, line: { type: 'none' } })
  s.addText(t, { x: 9.7, y: y + 0.18, w: 2.95, h: 0.4, fontFace: F, fontSize: 13.5, bold: true, color: INK })
  s.addText(d, { x: 9.7, y: y + 0.58, w: 2.95, h: 0.5, fontFace: F, fontSize: 11, color: MUTED })
})
footer(s, 4)

// ============ 5. 新岗位发现 ============
s = p.addSlide(); base(s); header(s, '04', '新岗位发现与定义', '多源联网检索 + 大模型 RAG 接地，识别萌芽中的新兴岗位')
const flow = ['关键词/种子', 'Tavily + Serper\n双源检索证据', '大模型基于证据\n生成结构化定义', '能力项证据校验\n置信度评估', '入库 · 全景图谱\n琥珀高亮']
flow.forEach((t, i) => {
  const x = 0.55 + i * 2.55
  card(s, x, 1.6, 2.3, 1.3)
  s.addText(t, { x: x + 0.05, y: 1.6, w: 2.2, h: 1.3, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, bold: i === 0, color: INK })
  if (i < 4) s.addText('→', { x: x + 2.28, y: 1.6, w: 0.3, h: 1.3, align: 'center', valign: 'middle', fontFace: F, fontSize: 16, color: INDIGO })
})
card(s, 0.55, 3.2, 12.2, 3.5)
s.addText('生成的岗位定义包含', { x: 0.85, y: 3.4, w: 5, h: 0.4, fontFace: F, fontSize: 14, bold: true, color: INDIGO })
const def = ['岗位名称', '核心职责', '必备技能', '加分技能', '典型行业应用场景']
def.forEach((t, i) => {
  s.addShape(p.ShapeType.roundRect, { x: 0.85 + i * 2.4, y: 3.85, w: 2.2, h: 0.55, rectRadius: 0.1, fill: { type: 'solid', color: 'E0E9FF' }, line: { type: 'none' } })
  s.addText(t, { x: 0.85 + i * 2.4, y: 3.85, w: 2.2, h: 0.55, align: 'center', valign: 'middle', fontFace: F, fontSize: 12.5, bold: true, color: INDIGO })
})
s.addText([
  { text: '示例｜AI智能体开发工程师', options: { bold: true, color: INK, fontSize: 14, breakLine: true, paraSpaceAfter: 6 } },
  { text: '必备技能：Python · PyTorch · 智能体 · 模型微调 · 向量数据库 · Docker · 分布式系统', options: { color: MUTED, fontSize: 12.5, breakLine: true, paraSpaceAfter: 4 } },
  { text: '典型场景：智能对话与客服机器人 · 智能推荐 · 竞品分析与市场调研   （13 条多源证据 · 3 个独立来源交叉验证）', options: { color: MUTED, fontSize: 12.5 } },
], { x: 0.85, y: 4.7, w: 11.6, h: 1.8, fontFace: F, valign: 'top', lineSpacingMultiple: 1.2 })
s.addText('✓ 当前 6 个新兴岗位全部有权威佐证（人社部文件/头部报告），标注「新出现 / 复兴」徽章，支持人工优化与动态更新', { x: 0.85, y: 6.25, w: 11.6, h: 0.35, fontFace: F, fontSize: 12, bold: true, color: EMERALD })
footer(s, 5)

// ============ 6. 既有岗位能力演化 ============
s = p.addSlide(); base(s); header(s, '05', '既有岗位能力动态更新', '2018 历史基线 → 2026 现网真实 JD 驱动演化 · 自动标注变更并溯源')
const chg = [
  [EMERALD, '新增 Add', '识别新出现且经交叉验证的能力项', '如 Java 工程师新增「大语言模型应用 / RAG / 容器化部署」'],
  [ROSE, '删除 Delete', '近期数据不再要求的过时能力', '标记 deprecated，保留历史版本可追溯'],
  [AMBER, '修改 Modify', '重要度（必备↔加分）或权重显著变化', '反映市场需求热度的上升或下降'],
]
chg.forEach(([c, t, d, e], i) => {
  const y = 1.6 + i * 1.65
  card(s, 0.55, y, 12.2, 1.5)
  s.addShape(p.ShapeType.roundRect, { x: 0.8, y: y + 0.32, w: 1.7, h: 0.85, rectRadius: 0.12, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(t, { x: 0.8, y: y + 0.32, w: 1.7, h: 0.85, align: 'center', valign: 'middle', fontFace: F, fontSize: 14, bold: true, color: 'FFFFFF' })
  s.addText(d, { x: 2.75, y: y + 0.28, w: 9.7, h: 0.5, fontFace: F, fontSize: 15, bold: true, color: INK })
  s.addText(e, { x: 2.75, y: y + 0.8, w: 9.7, h: 0.5, fontFace: F, fontSize: 12.5, color: MUTED })
})
s.addText('每条变更均附「更新说明 + 数据源 + 置信度」，岗位版本号自增，前端时间线可视化展示', {
  x: 0.55, y: 6.7, w: 12.2, h: 0.4, align: 'center', fontFace: F, fontSize: 12.5, bold: true, color: INDIGO })
footer(s, 6)

// ============ 6.5 分级演化：初/中/高晋升路径 ============
s = p.addSlide(); base(s); header(s, '05+', '分级演化 · 初/中/高晋升路径', '32 / 32 个岗位建有三档分级画像 · 621 条真实演化记录贯穿 2018→2024→2026 三个切片')
const lvls = [
  [CYAN, '初级 Junior', '0-3 年 · 基础技术栈与工程规范'],
  [INDIGO, '中级 Middle', '3-5 年 · 独立负责模块与核心技能'],
  [VIOLET, '高级 Senior', '5 年以上 · 架构能力与技术引领'],
]
lvls.forEach(([c, t, d], i) => {
  const x = 0.55 + i * 4.2, y = 1.7 + i * 0.55
  card(s, x, y, 3.75, 1.5)
  s.addShape(p.ShapeType.roundRect, { x: x + 0.25, y: y + 0.25, w: 1.9, h: 0.5, rectRadius: 0.25, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(t, { x: x + 0.25, y: y + 0.25, w: 1.9, h: 0.5, align: 'center', valign: 'middle', fontFace: F, fontSize: 13, bold: true, color: 'FFFFFF' })
  s.addText(d, { x: x + 0.25, y: y + 0.82, w: 3.3, h: 0.6, fontFace: F, fontSize: 11, color: MUTED })
  if (i < 2) s.addText('▶', { x: x + 3.75, y: y + 0.55, w: 0.45, h: 0.6, align: 'center', valign: 'middle', fontFace: F, fontSize: 16, color: FAINT })
})
card(s, 0.55, 3.95, 12.2, 2.65)
s.addText('晋升 diff（复用演化引擎的级别差异计算）｜示例：AI智能体开发工程师 中级 → 高级', { x: 0.85, y: 4.15, w: 11.6, h: 0.4, fontFace: F, fontSize: 14, bold: true, color: INDIGO })
s.addText([
  { text: '🟢 晋升需新增掌握：云原生 等 8 条能力项', options: { breakLine: true, paraSpaceAfter: 6, color: INK, bold: true } },
  { text: '🟡 晋升要求强化：核心技能权重显著上调（如 45% → 72%）', options: { breakLine: true, paraSpaceAfter: 6, color: MUTED } },
  { text: '⚪ 部分基础技能在高级 JD 中不再单列 —— 视为默认前提', options: { breakLine: true, paraSpaceAfter: 6, color: MUTED } },
  { text: '级别由真实 JD 标题关键词 + 经验年限自动推断分桶，每档 ≥3 条 JD 才产出画像，前端以「初级→中级→高级」阶梯视图展示', options: { color: FAINT, fontSize: 11 } },
], { x: 0.85, y: 4.65, w: 11.6, h: 1.85, fontFace: F, fontSize: 12.5, lineSpacingMultiple: 1.15 })
footer(s, 7)

// ============ 7. 人岗匹配诊断 ============
s = p.addSlide(); base(s); header(s, '06', '人岗匹配诊断与差距分析', '简历解析 → 多维匹配 → 差距诊断 → 学习路径 + 改进建议')
card(s, 0.55, 1.45, 7.7, 5.4)
s.addImage({ path: '03_人岗匹配诊断.jpeg', x: 0.7, y: 1.6, w: 7.4, h: 5.1, sizing: { type: 'contain', w: 7.4, h: 5.1 } })
const mpts = [
  ['简历解析', 'PDF / Word / 文本，提取召回 100%'],
  ['多维度匹配', '必备覆盖 · 加分覆盖 · 级别 · 领域相关'],
  ['差距分析', '已具备 vs 能力缺口，清晰可视'],
  ['学习路径', '按技能先修关系拓扑排序分步规划'],
  ['改进建议', '大模型生成资源方向与达标周期'],
]
mpts.forEach(([t, d], i) => {
  const y = 1.55 + i * 1.06
  card(s, 8.45, y, 4.3, 0.92)
  s.addText(t, { x: 8.65, y: y + 0.12, w: 3.9, h: 0.35, fontFace: F, fontSize: 13.5, bold: true, color: INDIGO })
  s.addText(d, { x: 8.65, y: y + 0.46, w: 3.95, h: 0.4, fontFace: F, fontSize: 10.5, color: MUTED })
})
footer(s, 7)

// ============ 8. 创新① 多源清洗 ============
s = p.addSlide(); base(s); header(s, '07', '创新① 多源异构数据清洗与交叉验证', '解决招聘数据的 时滞 / 噪音 / 抄袭 / 通胀')
const clean = [
  [ROSE, '抄袭 / 重复', '64位 SimHash 海明距离 + 精确哈希近似去重', '8/8 抄袭样本检出，排除出交叉验证'],
  [AMBER, '能力通胀', '技能数远超中位数 + 非共识技能占比高', '17/17 通胀样本检出，噪声技能被过滤'],
  [CYAN, '时滞 Lag', '发布时间衰减，新鲜度指数加权', '旧数据降权，半衰期 180 天'],
  [VIOLET, '噪音 Noise', '同义词归一化 + 后缀裁剪', '"微服务架构"→"微服务"，合并碎片'],
]
clean.forEach(([c, t, m, r], i) => {
  const x = 0.55 + (i % 2) * 6.15, y = 1.55 + Math.floor(i / 2) * 2.55
  card(s, x, y, 5.95, 2.35)
  s.addShape(p.ShapeType.roundRect, { x: x + 0.3, y: y + 0.3, w: 2.0, h: 0.6, rectRadius: 0.3, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(t, { x: x + 0.3, y: y + 0.3, w: 2.0, h: 0.6, align: 'center', valign: 'middle', fontFace: F, fontSize: 14, bold: true, color: 'FFFFFF' })
  s.addText([{ text: '方法：', options: { bold: true, color: INK } }, { text: m, options: { color: MUTED } }], { x: x + 0.3, y: y + 1.05, w: 5.35, h: 0.55, fontFace: F, fontSize: 12.5, lineSpacingMultiple: 1.1 })
  s.addText([{ text: '效果：', options: { bold: true, color: EMERALD } }, { text: r, options: { color: MUTED } }], { x: x + 0.3, y: y + 1.65, w: 5.35, h: 0.55, fontFace: F, fontSize: 12.5, lineSpacingMultiple: 1.1 })
})
footer(s, 8)

// ============ 9. 创新② 幻觉防控 ============
s = p.addSlide(); base(s); header(s, '08', '创新② 能力"幻觉"防控', '只有被多源交叉验证的能力才高置信进入图谱，且可溯源')
s.addShape(p.ShapeType.roundRect, { x: 0.55, y: 1.55, w: 12.2, h: 0.95, rectRadius: 0.1, fill: { type: 'solid', color: '0F172A' }, line: { type: 'none' }, shadow })
s.addText('C = 0.35·支持率 + 0.20·来源多样性 + 0.15·时效性 + 0.20·来源权威度 + 0.10·外部验证', {
  x: 0.55, y: 1.55, w: 12.2, h: 0.95, align: 'center', valign: 'middle', fontFace: 'Consolas', fontSize: 16, bold: true, color: '7DD3FC' })
const ctrl = [
  [INDIGO, '交叉验证门槛', '必备技能要求 ≥2 个独立非重复来源，否则自动降级为加分技能 —— 防止单源臆造被定义为"必备"'],
  [CYAN, '置信度过滤', '低于阈值（0.45）的能力项标记为候选，不进入正式图谱 —— 过滤潜在幻觉 / 噪音'],
  [EMERALD, '证据溯源', '每个能力项保留多条证据（JD 片段 / 外部链接），前端可逐项展开追溯 —— 可解释、可审计'],
]
ctrl.forEach(([c, t, d], i) => {
  const y = 2.8 + i * 1.25
  card(s, 0.55, y, 12.2, 1.1)
  s.addShape(p.ShapeType.ellipse, { x: 0.85, y: y + 0.32, w: 0.45, h: 0.45, fill: { type: 'solid', color: c }, line: { type: 'none' } })
  s.addText(String(i + 1), { x: 0.85, y: y + 0.32, w: 0.45, h: 0.45, align: 'center', valign: 'middle', fontFace: F, fontSize: 13, bold: true, color: 'FFFFFF' })
  s.addText(t, { x: 1.5, y: y + 0.15, w: 3.0, h: 0.8, valign: 'middle', fontFace: F, fontSize: 15, bold: true, color: INK })
  s.addText(d, { x: 4.5, y: y + 0.15, w: 8.0, h: 0.8, valign: 'middle', fontFace: F, fontSize: 12.5, color: MUTED, lineSpacingMultiple: 1.1 })
})
s.addText('+ 嵌入模型退化守卫：识别中文向量模型对英文token的退化，语义匹配分类准确率 86.9% → 100%', {
  x: 0.55, y: 6.65, w: 12.2, h: 0.4, align: 'center', fontFace: F, fontSize: 11.5, bold: true, color: VIOLET })
footer(s, 9)

// ============ 9.5 统一置信度公式（五因子 + 算例） ============
s = p.addSlide(); base(s); header(s, '08+', '统一置信度公式 · 每一分都可解释', '五因子线性加权（services/confidence.py 全系统唯一）· 因子逐项落库 · 前端点击徽章可见分解')
const facs = [
  [INDIGO, '支持率', '0.35', '提及技能的独立 JD 数 ÷ 有效 JD 总数'],
  [CYAN, '来源多样性', '0.20', '独立平台数 ÷ 3 封顶（真实语料校准后冻结）'],
  [EMERALD, '时效性', '0.15', '新鲜度均值，指数衰减半衰期 180 天'],
  [VIOLET, '来源权威度', '0.20', '官网/政府 1.0 · 数据集 0.7 · 网络 0.6'],
  [AMBER, '外部验证', '0.10', '联网检索 / 权威文件佐证记 1，否则 0'],
]
facs.forEach(([c, t, w, d], i) => {
  const x = 0.55 + i * 2.48
  card(s, x, 1.6, 2.28, 2.15)
  s.addText(w, { x, y: 1.78, w: 2.28, h: 0.55, align: 'center', fontFace: F, fontSize: 24, bold: true, color: c })
  s.addText(t, { x, y: 2.38, w: 2.28, h: 0.4, align: 'center', fontFace: F, fontSize: 13.5, bold: true, color: INK })
  s.addText(d, { x: x + 0.12, y: 2.8, w: 2.04, h: 0.9, align: 'center', fontFace: F, fontSize: 9.5, color: MUTED, lineSpacingMultiple: 1.1 })
})
card(s, 0.55, 4.05, 12.2, 1.9)
s.addText('算例（真实数据）｜技能「大语言模型」', { x: 0.85, y: 4.22, w: 11.6, h: 0.4, fontFace: F, fontSize: 14, bold: true, color: INDIGO })
s.addText('9/13 条有效 JD 提及（支持率 0.69）· 2 个平台（多样性 0.67）· 平均新鲜度 0.88 · 全部企业官网（权威度 1.0）· 暂无外部验证（0）', {
  x: 0.85, y: 4.66, w: 11.6, h: 0.4, fontFace: F, fontSize: 12, color: MUTED })
s.addText('C = 0.35×0.69 + 0.20×0.67 + 0.15×0.88 + 0.20×1.0 + 0.10×0 ≈ 0.71', {
  x: 0.85, y: 5.12, w: 11.6, h: 0.6, align: 'center', valign: 'middle', fontFace: 'Consolas', fontSize: 16, bold: true, color: '0F172A',
  fill: { type: 'solid', color: 'E0F2FE' }, line: { type: 'none' } })
s.addText('岗位整体置信度 = 粗粒度 active 能力项的权重加权平均；全库岗位均值 0.5613 —— 比上一版低，因为 6 个原本靠联网检索打分（最高 1.000）的新兴岗位已改由真实 JD 语料重建、纳入同一条交叉验证链，公式一个参数未改（3258 项单来源细粒度技能点降级为 candidate，保留可查但不进图谱主视图）', {
  x: 0.55, y: 6.2, w: 12.2, h: 0.6, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, bold: true, color: INDIGO,
  fill: { type: 'solid', color: 'E0E9FF' }, line: { type: 'none' } })
footer(s, 10)

// ============ 10. 测试与指标 ============
s = p.addSlide(); base(s); header(s, '09', '测试与验证', '双轨数据集（对抗基准 379 条 + 真实语料 2570 条）· 三项核心指标全部超过 90%')
const metrics = [
  ['98.24%', 'JD 解析准确率', 'F1 · 371 条非重复 JD', EMERALD],
  ['96.49%', '简历提取准确率', 'F1 · 召回 100%', EMERALD],
  ['100%', '人岗匹配准确率', '必备技能判定分类准确率', EMERALD],
]
metrics.forEach(([v, t, d, c], i) => {
  const x = 0.6 + i * 4.15
  card(s, x, 1.6, 3.85, 2.5)
  s.addText(v, { x, y: 1.9, w: 3.85, h: 1.0, align: 'center', fontFace: F, fontSize: 48, bold: true, color: c })
  s.addText(t, { x, y: 3.0, w: 3.85, h: 0.45, align: 'center', fontFace: F, fontSize: 16, bold: true, color: INK })
  s.addText(d, { x, y: 3.5, w: 3.85, h: 0.4, align: 'center', fontFace: F, fontSize: 11.5, color: MUTED })
  s.addText('要求 ≥ 90% ✓', { x, y: 3.78, w: 3.85, h: 0.3, align: 'center', fontFace: F, fontSize: 11, bold: true, color: EMERALD })
})
const extra = [['8 / 8', '抄袭检出'], ['17 / 17', '通胀检出'], ['73%', '单元测试覆盖率'], ['146', '测试用例全通过']]
extra.forEach(([v, l], i) => {
  const x = 0.6 + i * 3.1
  card(s, x, 4.35, 2.9, 1.5)
  s.addText(v, { x, y: 4.55, w: 2.9, h: 0.7, align: 'center', fontFace: F, fontSize: 26, bold: true, color: INDIGO })
  s.addText(l, { x, y: 5.25, w: 2.9, h: 0.4, align: 'center', fontFace: F, fontSize: 12, color: MUTED })
})
s.addText('测试数据与评测脚本可一键复现：run_collect → import_raw → run_pipeline --from-db → evaluate all → pytest --cov', {
  x: 0.6, y: 6.15, w: 12.1, h: 0.5, align: 'center', valign: 'middle', fontFace: F, fontSize: 12, color: MUTED,
  fill: { type: 'solid', color: 'FFFFFF' }, line: { color: LINE, width: 1 } })
footer(s, 10)

// ============ 11. 部署 + 总结 ============
s = p.addSlide(); base(s); header(s, '10', '工程化部署与价值', '轻量部署 · 已稳定上线 · 可迁移可进化')
card(s, 0.55, 1.55, 5.95, 2.45)
s.addText('轻量化部署', { x: 0.85, y: 1.75, w: 5, h: 0.4, fontFace: F, fontSize: 16, bold: true, color: INDIGO })
s.addText([
  '· 后端单进程同端口托管前端，常驻内存约 162MB',
  '· systemd 内存上限 420MB 保护小内存服务器',
  '· 提供 Dockerfile / docker-compose 容器化部署',
  '· 已上线：http://101.200.184.201:8200',
].map((t, i) => ({ text: t, options: { breakLine: true, paraSpaceAfter: 7 } })), { x: 0.85, y: 2.25, w: 5.4, h: 1.6, fontFace: F, fontSize: 12.5, color: MUTED })
card(s, 6.8, 1.55, 5.95, 2.45)
s.addText('实用价值与可迁移性', { x: 7.1, y: 1.75, w: 5, h: 0.4, fontFace: F, fontSize: 16, bold: true, color: VIOLET })
s.addText([
  '· 全流程闭环，有效解决企业招聘与人岗匹配痛点',
  '· 技术方案与岗位无关，可迁移至医疗/金融/制造',
  '· 数据合规：简历个人信息不留存，能力项可溯源审计',
  '· 可对接企业人才盘点与培训体系',
].map((t) => ({ text: t, options: { breakLine: true, paraSpaceAfter: 7 } })), { x: 7.1, y: 2.25, w: 5.4, h: 1.6, fontFace: F, fontSize: 12.5, color: MUTED })
s.addShape(p.ShapeType.roundRect, { x: 0.55, y: 4.25, w: 12.2, h: 2.45, rectRadius: 0.12, fill: { type: 'solid', color: INDIGO }, line: { type: 'none' }, shadow })
s.addText('让人才与岗位，精准相遇', { x: 0.55, y: 4.6, w: 12.2, h: 0.7, align: 'center', fontFace: F, fontSize: 26, bold: true, color: 'FFFFFF' })
s.addText('数据驱动 + 大模型 + 知识图谱  ·  多源交叉验证  ·  反幻觉防控  ·  数据合规  ·  动态演化', { x: 0.55, y: 5.4, w: 12.2, h: 0.4, align: 'center', fontFace: F, fontSize: 14, color: 'DBEAFE' })
s.addText('在线体验：http://101.200.184.201:8200', { x: 0.55, y: 5.95, w: 12.2, h: 0.45, align: 'center', fontFace: F, fontSize: 15, bold: true, color: 'FFFFFF' })
footer(s, 11)

p.writeFile({ fileName: '智岗图谱_作品介绍.pptx' }).then(f => console.log('PPT generated:', f))
