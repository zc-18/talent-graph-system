// 配色硬门槛审计：WCAG 2.x 对比度 + CIEDE2000 色差 + LCh 色相角
// 直接从 build.js 解析色值，保证脚本与实际产物不漂移。
// 用法：node palette_audit.js
const fs = require('fs'), path = require('path')
const SRC = fs.readFileSync(path.join(__dirname, 'build.js'), 'utf8')

function tok(name) {
  const m = SRC.match(new RegExp('\\b' + name + "\\s*=\\s*'([0-9A-Fa-f]{6})'"))
  if (!m) throw new Error('找不到色值 token: ' + name)
  return m[1].toUpperCase()
}
const T = {}
for (const n of ['INK','MUTED','FAINT','INDIGO','CYAN','VIOLET','EMERALD','ROSE','AMBER','CARD','PAGEBG','LINE',
                 'TINT_I','TINT_V','TINT_E','TINT_S','ONINDIGO'])
  { try { T[n] = tok(n) } catch (e) { T[n] = null } }
// 兼容尚未 token 化的旧色值
T.TINT_I = T.TINT_I || 'E0E9FF'; T.TINT_V = T.TINT_V || 'EDE9FE'
T.TINT_E = T.TINT_E || 'E8F7F2'; T.TINT_S = T.TINT_S || 'E0F2FE'
T.ONINDIGO = T.ONINDIGO || 'DBEAFE'

// 试色模式：PALETTE_OVERRIDE="INDIGO=5B45D0,CYAN=0E6BA8" node palette_audit.js
if (process.env.PALETTE_OVERRIDE) {
  for (const kv of process.env.PALETTE_OVERRIDE.split(',')) {
    const [k, v] = kv.split('=').map(x => x.trim())
    if (k && v) T[k.toUpperCase()] = v.toUpperCase()
  }
  console.log('[试色模式] 覆盖：' + process.env.PALETTE_OVERRIDE)
}

// ---- 色彩数学 ----
const rgb = h => [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16))
const lin = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4) }
const lum = h => { const [r, g, b] = rgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const cr = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05) }

function lab(h) { // sRGB -> CIELAB (D65)
  const [r, g, b] = rgb(h).map(lin)
  let X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
  let Y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.00000
  let Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
  const f = t => t > 0.008856 ? Math.cbrt(t) : (7.787 * t + 16 / 116)
  X = f(X); Y = f(Y); Z = f(Z)
  return [116 * Y - 16, 500 * (X - Y), 200 * (Y - Z)]
}
const lch = h => {
  const [L, a, b] = lab(h); let H = Math.atan2(b, a) * 180 / Math.PI; if (H < 0) H += 360
  return [L, Math.hypot(a, b), H]
}

function de2000(h1, h2) { // CIEDE2000
  const [L1, a1, b1] = lab(h1), [L2, a2, b2] = lab(h2)
  const kL = 1, kC = 1, kH = 1
  const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2), Cb = (C1 + C2) / 2
  const G = 0.5 * (1 - Math.sqrt(Math.pow(Cb, 7) / (Math.pow(Cb, 7) + Math.pow(25, 7))))
  const ap1 = (1 + G) * a1, ap2 = (1 + G) * a2
  const Cp1 = Math.hypot(ap1, b1), Cp2 = Math.hypot(ap2, b2)
  const rad = x => x * Math.PI / 180, deg = x => x * 180 / Math.PI
  let hp1 = (b1 === 0 && ap1 === 0) ? 0 : deg(Math.atan2(b1, ap1)); if (hp1 < 0) hp1 += 360
  let hp2 = (b2 === 0 && ap2 === 0) ? 0 : deg(Math.atan2(b2, ap2)); if (hp2 < 0) hp2 += 360
  const dLp = L2 - L1, dCp = Cp2 - Cp1
  let dhp = 0
  if (Cp1 * Cp2 !== 0) { dhp = hp2 - hp1; if (dhp > 180) dhp -= 360; else if (dhp < -180) dhp += 360 }
  const dHp = 2 * Math.sqrt(Cp1 * Cp2) * Math.sin(rad(dhp) / 2)
  const Lbp = (L1 + L2) / 2, Cbp = (Cp1 + Cp2) / 2
  let hbp
  if (Cp1 * Cp2 === 0) hbp = hp1 + hp2
  else { hbp = (hp1 + hp2) / 2; if (Math.abs(hp1 - hp2) > 180) hbp += (hp1 + hp2 < 360 ? 180 : -180) }
  const Tt = 1 - 0.17 * Math.cos(rad(hbp - 30)) + 0.24 * Math.cos(rad(2 * hbp))
    + 0.32 * Math.cos(rad(3 * hbp + 6)) - 0.20 * Math.cos(rad(4 * hbp - 63))
  const dTh = 30 * Math.exp(-Math.pow((hbp - 275) / 25, 2))
  const Rc = 2 * Math.sqrt(Math.pow(Cbp, 7) / (Math.pow(Cbp, 7) + Math.pow(25, 7)))
  const Sl = 1 + (0.015 * Math.pow(Lbp - 50, 2)) / Math.sqrt(20 + Math.pow(Lbp - 50, 2))
  const Sc = 1 + 0.045 * Cbp, Sh = 1 + 0.015 * Cbp * Tt
  const Rt = -Math.sin(rad(2 * dTh)) * Rc
  return Math.sqrt(Math.pow(dLp / (kL * Sl), 2) + Math.pow(dCp / (kC * Sc), 2) + Math.pow(dHp / (kH * Sh), 2)
    + Rt * (dCp / (kC * Sc)) * (dHp / (kH * Sh)))
}

// ---- 逐页盘点 build.js 中真实出现的 前景/背景 对 ----
// 门槛：正文/数字 4.5:1；纯装饰大号标题（>=24pt，或 >=20pt bold） 3.0:1
const P = T
const pairs = [
  ['P01 封面', '岗位药丸文字 INDIGO/白', P.INDIGO, 'FFFFFF', 4.5],
  ['P02 痛点', '图标 白/ROSE 30pt', 'FFFFFF', P.ROSE, 3.0],
  ['P02 痛点', '图标 白/AMBER 30pt', 'FFFFFF', P.AMBER, 3.0],
  ['P02 痛点', '图标 白/VIOLET 30pt', 'FFFFFF', P.VIOLET, 3.0],
  ['P02 痛点', '小标题 MUTED/白卡 13pt', P.MUTED, 'FFFFFF', 4.5],
  ['P02 痛点', '大标题 INK/白卡 20pt', P.INK, 'FFFFFF', 3.0],
  ['P02 痛点', '描述 MUTED/白卡 13pt', P.MUTED, 'FFFFFF', 4.5],
  ['P02 痛点', '底部条 INDIGO/E0E9FF', P.INDIGO, P.TINT_I, 4.5],
  ['P03 闭环', '步骤号 INDIGO/白卡 14pt', P.INDIGO, 'FFFFFF', 4.5],
  ['P03 闭环', '步骤号 CYAN/白卡 14pt', P.CYAN, 'FFFFFF', 4.5],
  ['P03 闭环', '步骤号 VIOLET/白卡 14pt', P.VIOLET, 'FFFFFF', 4.5],
  ['P03 闭环', '步骤号 EMERALD/白卡 14pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P03 闭环', '步骤说明 MUTED/白卡 10pt', P.MUTED, 'FFFFFF', 4.5],
  ['P03 闭环', '箭头 FAINT/PAGEBG 12pt', P.FAINT, P.PAGEBG, 3.0],
  ['P03 闭环', 'KPI 数字 INDIGO/PAGEBG 32pt', P.INDIGO, P.PAGEBG, 3.0],
  ['P03 闭环', 'KPI 标签 MUTED/PAGEBG 13pt', P.MUTED, P.PAGEBG, 4.5],
  ['P03 闭环', '技术栈条 MUTED/白 12.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P04 采集', '渠道药丸 白/INDIGO 11.5pt', 'FFFFFF', P.INDIGO, 4.5],
  ['P04 采集', '渠道药丸 白/CYAN 11.5pt', 'FFFFFF', P.CYAN, 4.5],
  ['P04 采集', '渠道药丸 白/VIOLET 11.5pt', 'FFFFFF', P.VIOLET, 4.5],
  ['P04 采集', '渠道药丸 白/EMERALD 11.5pt', 'FFFFFF', P.EMERALD, 4.5],
  ['P04 采集', '渠道药丸 白/AMBER 11.5pt', 'FFFFFF', P.AMBER, 4.5],
  ['P04 采集', '渠道药丸 白/ROSE 11.5pt', 'FFFFFF', P.ROSE, 4.5],
  ['P04 采集', '条数 INDIGO/白卡 18pt', P.INDIGO, 'FFFFFF', 4.5],
  ['P04 采集', '条数 CYAN/白卡 18pt', P.CYAN, 'FFFFFF', 4.5],
  ['P04 采集', '条数 VIOLET/白卡 18pt', P.VIOLET, 'FFFFFF', 4.5],
  ['P04 采集', '条数 EMERALD/白卡 18pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P04 采集', '条数 AMBER/白卡 18pt', P.AMBER, 'FFFFFF', 4.5],
  ['P04 采集', '条数 ROSE/白卡 18pt', P.ROSE, 'FFFFFF', 4.5],
  ['P04 采集', '渠道说明 MUTED/白卡 9.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P04 采集', '护栏正文 MUTED/白卡 11pt', P.MUTED, 'FFFFFF', 4.5],
  ['P04 采集', '权威佐证 EMERALD/E8F7F2', P.EMERALD, P.TINT_E, 4.5],
  ['P04 采集', '雇主识别 INDIGO/EDE9FE', P.INDIGO, P.TINT_V, 4.5],
  ['P04 采集', 'RoleContract MUTED/PAGEBG', P.MUTED, P.PAGEBG, 4.5],
  ['P05 图谱', '要点标题 INK/白卡 13.5pt', P.INK, 'FFFFFF', 4.5],
  ['P05 图谱', '要点描述 MUTED/白卡 11pt', P.MUTED, 'FFFFFF', 4.5],
  ['P06 发现', '流程卡 INK/白卡 12pt', P.INK, 'FFFFFF', 4.5],
  ['P06 发现', '箭头 INDIGO/PAGEBG 16pt', P.INDIGO, P.PAGEBG, 3.0],
  ['P06 发现', '要素药丸 INDIGO/E0E9FF', P.INDIGO, P.TINT_I, 4.5],
  ['P06 发现', '示例正文 MUTED/白卡 12.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P06 发现', '佐证行 EMERALD/白卡 12pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P07 演化', '标签 白/EMERALD 14pt', 'FFFFFF', P.EMERALD, 4.5],
  ['P07 演化', '标签 白/ROSE 14pt', 'FFFFFF', P.ROSE, 4.5],
  ['P07 演化', '标签 白/AMBER 14pt', 'FFFFFF', P.AMBER, 4.5],
  ['P07 演化', '说明 MUTED/白卡 12.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P07 演化', '脚注 INDIGO/PAGEBG 12.5pt', P.INDIGO, P.PAGEBG, 4.5],
  ['P08 分级', '级别药丸 白/CYAN 13pt', 'FFFFFF', P.CYAN, 4.5],
  ['P08 分级', '级别药丸 白/INDIGO 13pt', 'FFFFFF', P.INDIGO, 4.5],
  ['P08 分级', '级别药丸 白/VIOLET 13pt', 'FFFFFF', P.VIOLET, 4.5],
  ['P08 分级', '级别说明 MUTED/白卡 11pt', P.MUTED, 'FFFFFF', 4.5],
  ['P08 分级', 'diff 尾注 MUTED/白卡 11pt', P.MUTED, 'FFFFFF', 4.5],
  ['P09 匹配', '要点标题 INDIGO/白卡 13.5pt', P.INDIGO, 'FFFFFF', 4.5],
  ['P09 匹配', '要点描述 MUTED/白卡 10.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P10 清洗', '类别药丸 白/ROSE 14pt', 'FFFFFF', P.ROSE, 4.5],
  ['P10 清洗', '类别药丸 白/AMBER 14pt', 'FFFFFF', P.AMBER, 4.5],
  ['P10 清洗', '类别药丸 白/CYAN 14pt', 'FFFFFF', P.CYAN, 4.5],
  ['P10 清洗', '类别药丸 白/VIOLET 14pt', 'FFFFFF', P.VIOLET, 4.5],
  ['P10 清洗', '效果: EMERALD/白卡 12.5pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P11 幻觉', '公式 7DD3FC/0F172A 16pt', '7DD3FC', '0F172A', 4.5],
  ['P11 幻觉', '序号 白/INDIGO 圆点 13pt', 'FFFFFF', P.INDIGO, 4.5],
  ['P11 幻觉', '序号 白/CYAN 圆点 13pt', 'FFFFFF', P.CYAN, 4.5],
  ['P11 幻觉', '序号 白/EMERALD 圆点 13pt', 'FFFFFF', P.EMERALD, 4.5],
  ['P11 幻觉', '条目描述 MUTED/白卡 12.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P11 幻觉', '守卫脚注 VIOLET/PAGEBG 11.5pt', P.VIOLET, P.PAGEBG, 4.5],
  ['P12 置信', '权重 INDIGO/白卡 24pt', P.INDIGO, 'FFFFFF', 4.5],
  ['P12 置信', '权重 CYAN/白卡 24pt', P.CYAN, 'FFFFFF', 4.5],
  ['P12 置信', '权重 EMERALD/白卡 24pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P12 置信', '权重 VIOLET/白卡 24pt', P.VIOLET, 'FFFFFF', 4.5],
  ['P12 置信', '权重 AMBER/白卡 24pt', P.AMBER, 'FFFFFF', 4.5],
  ['P12 置信', '因子说明 MUTED/白卡 9.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P12 置信', '算式 0F172A/E0F2FE 16pt', '0F172A', P.TINT_S, 4.5],
  ['P12 置信', 'R6 结论 INDIGO/EDE9FE 11.5pt', P.INDIGO, P.TINT_V, 4.5],
  ['P13 测试', '指标 EMERALD/白卡 48pt', P.EMERALD, 'FFFFFF', 3.0],
  ['P13 测试', '要求>=90% EMERALD/白 11pt', P.EMERALD, 'FFFFFF', 4.5],
  ['P13 测试', '附加数字 INDIGO/白卡 26pt', P.INDIGO, 'FFFFFF', 3.0],
  ['P13 测试', '附加标签 MUTED/白卡 12pt', P.MUTED, 'FFFFFF', 4.5],
  ['P14 价值', '小标题 INDIGO/白卡 16pt', P.INDIGO, 'FFFFFF', 4.5],
  ['P14 价值', '小标题 VIOLET/白卡 16pt', P.VIOLET, 'FFFFFF', 4.5],
  ['P14 价值', '列表 MUTED/白卡 12.5pt', P.MUTED, 'FFFFFF', 4.5],
  ['P14 价值', '标语 白/INDIGO 26pt', 'FFFFFF', P.INDIGO, 3.0],
  ['P14 价值', '副标语 DBEAFE/INDIGO 14pt', 'DBEAFE', P.INDIGO, 4.5],
  ['全局', '页眉编号 白/INDIGO 20pt', 'FFFFFF', P.INDIGO, 3.0],
  ['全局', '页眉标题 INK/PAGEBG 26pt', P.INK, P.PAGEBG, 3.0],
  ['全局', '页眉副标 MUTED/PAGEBG 12.5pt', P.MUTED, P.PAGEBG, 4.5],
  ['全局', '右上水印 FAINT/PAGEBG 10pt', P.FAINT, P.PAGEBG, 3.0],
  ['全局', '页脚 URL+页码 FAINT/PAGEBG 9pt', P.FAINT, P.PAGEBG, 3.0],
]

const pad = (s, n) => { // 中文按 2 列宽对齐
  s = String(s); let w = 0
  for (const ch of s) w += /[⺀-￿]/.test(ch) ? 2 : 1
  return s + ' '.repeat(Math.max(1, n - w))
}

let fail = 0
const failRows = []
console.log('\n=== 一、对比度硬门槛（WCAG 2.x 相对亮度法） ===')
console.log(pad('页面 / 元素', 40) + pad('前景', 10) + pad('背景', 10) + pad('实测', 11) + pad('门槛', 7) + '判定')
console.log('-'.repeat(92))
for (const [pg, el, fg, bg, min] of pairs) {
  const v = cr(fg, bg)
  const ok = v >= min
  if (!ok) { fail++; failRows.push([pg, el, fg, bg, v, min]) }
  console.log(pad(pg + ' ' + el, 40) + pad('#' + fg, 10) + pad('#' + bg, 10)
    + pad(v.toFixed(2) + ':1', 11) + pad(min.toFixed(1), 7) + (ok ? 'PASS' : '**FAIL**'))
}
console.log('-'.repeat(92))
console.log('合计 ' + pairs.length + ' 对，未达标 ' + fail + ' 对')

console.log('\n=== 二、语义色可分性 ===')
const sem = ['INDIGO', 'CYAN', 'VIOLET', 'EMERALD', 'AMBER', 'ROSE']
console.log(pad('token', 10) + pad('HEX', 10) + pad('L*', 8) + pad('C*', 8) + pad('H°色相', 10) + '白底对比度')
for (const n of sem) {
  const [L, C, Hh] = lch(T[n])
  console.log(pad(n, 10) + pad('#' + T[n], 10) + pad(L.toFixed(1), 8) + pad(C.toFixed(1), 8)
    + pad(Hh.toFixed(1), 10) + cr(T[n], 'FFFFFF').toFixed(2) + ':1')
}
console.log('\n两两并列可分性（门槛：ΔE2000 >= 20 且 色相角差 >= 30°，后者保证不靠明度硬撑）')
console.log(pad('配对', 24) + pad('ΔE2000', 10) + pad('ΔH°', 9) + '判定')
console.log('-'.repeat(72))
let sfail = 0
const semFail = []
for (let i = 0; i < sem.length; i++) for (let j = i + 1; j < sem.length; j++) {
  const a = sem[i], b = sem[j]
  const d = de2000(T[a], T[b])
  let dh = Math.abs(lch(T[a])[2] - lch(T[b])[2]); if (dh > 180) dh = 360 - dh
  const ok = d >= 20 && dh >= 30
  if (!ok) { sfail++; semFail.push([a, b, d, dh]) }
  const why = ok ? 'PASS' : (d < 20 && dh < 30 ? '**FAIL 色差+色相双不足**' : (d < 20 ? '**FAIL ΔE 不足**' : '**FAIL 色相太近**'))
  console.log(pad(a + ' / ' + b, 24) + pad(d.toFixed(1), 10) + pad(dh.toFixed(1), 9) + why)
}
console.log('-'.repeat(72))
console.log('语义色配对 15 组，未达标 ' + sfail + ' 组')
console.log('\n>>> 总计未达标：对比度 ' + fail + ' 项 / 可分性 ' + sfail + ' 组\n')
if (fail) {
  console.log('对比度未达标明细：')
  for (const [pg, el, fg, bg, v, min] of failRows)
    console.log('  - ' + pg + ' ' + el + '  #' + fg + ' on #' + bg + '  ' + v.toFixed(2) + ':1 < ' + min)
}
if (sfail) {
  console.log('可分性未达标明细：')
  for (const [a, b, d, dh] of semFail)
    console.log('  - ' + a + ' / ' + b + '  ΔE=' + d.toFixed(1) + '  ΔH=' + dh.toFixed(1) + '°')
}
