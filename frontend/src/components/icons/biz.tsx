import { ReactNode, SVGProps } from 'react'

/**
 * 业务概念线性图标集（岗位 / 技能 / JD / 雇主 / 置信度 / 演化 / 图谱 / 匹配 / 人才 / 审核 …）
 *
 * 统一笔画规范，不要在这里破例：
 *   viewBox 24×24 · fill="none" · stroke="currentColor" · stroke-width 1.75
 *   圆角端点（linecap/linejoin = round） · 视觉安全区 2px（有效绘制区 2→22）
 *
 * 这些图标替代 lucide 里语义不贴切的通用图标（例如用 Copy 表示"抄袭拦截"、
 * 用 ShieldCheck 表示"置信度"）。颜色跟随 currentColor，尺寸用 className 控制。
 * 展示型双色调图标见同目录 duotone.tsx，两套通过 icons/index.tsx 统一出口。
 */
type IconProps = SVGProps<SVGSVGElement> & { className?: string }

function line(children: ReactNode) {
  return function LineIcon({ className = 'w-5 h-5', ...rest }: IconProps) {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
        strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true" {...rest}>
        {children}
      </svg>
    )
  }
}

/** 岗位：公文包 + 顶部把手，右下一颗定位点表示"某个具体职位" */
export const IJob = line(<>
  <rect x="3" y="7.5" width="18" height="12.5" rx="2.5" />
  <path d="M9 7.5V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1.5" />
  <path d="M3 12.5h18" />
  <path d="M10.5 12.5h3" />
</>)

/** 技能点：三层能力栈，上层高亮为"已验证的能力点" */
export const ISkill = line(<>
  <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
  <path d="M3 12.2 12 16.7l9-4.5" />
  <path d="M3 16.7 12 21.2l9-4.5" />
</>)

/** JD：招聘文档 + 折角 + 正文行 */
export const IJd = line(<>
  <path d="M14 2.8H6.8A1.8 1.8 0 0 0 5 4.6v14.8a1.8 1.8 0 0 0 1.8 1.8h10.4a1.8 1.8 0 0 0 1.8-1.8V7.8L14 2.8Z" />
  <path d="M13.8 3v4.6H19" />
  <path d="M8.4 12.4h7.2" />
  <path d="M8.4 16h4.8" />
</>)

/** 雇主：企业楼宇 + 窗格。交叉验证的"独立来源"计量单位 */
export const IEmployer = line(<>
  <path d="M3.5 21h17" />
  <path d="M5.5 21V5.4a1.4 1.4 0 0 1 1.4-1.4h6.2a1.4 1.4 0 0 1 1.4 1.4V21" />
  <path d="M14.5 21V9.6h3.6a1.4 1.4 0 0 1 1.4 1.4V21" />
  <path d="M8.4 8h3" />
  <path d="M8.4 12h3" />
  <path d="M8.4 16h3" />
</>)

/** 置信度：仪表盘指针，指向右上（高置信） */
export const IConfidence = line(<>
  <path d="M3.4 18a9 9 0 1 1 17.2 0" />
  <path d="M12 18l4.1-5.1" />
  <circle cx="12" cy="18" r="1.5" />
</>)

/** 演化：版本分叉 + 时间推进箭头 */
export const IEvolution = line(<>
  <circle cx="6" cy="18" r="2.2" />
  <circle cx="6" cy="6" r="2.2" />
  <circle cx="18" cy="12" r="2.2" />
  <path d="M6 8.2v7.6" />
  <path d="M8.2 6h3.3a4.3 4.3 0 0 1 4.3 4.3v.4" />
  <path d="M8.2 18h3.3a4.3 4.3 0 0 0 4.3-4.3v-.4" />
</>)

/** 图谱：中心节点 + 三个外围节点的连边 */
export const IGraph = line(<>
  <circle cx="12" cy="12" r="2.4" />
  <circle cx="5" cy="5.6" r="2.1" />
  <circle cx="19.2" cy="7.6" r="2.1" />
  <circle cx="15.6" cy="19.4" r="2.1" />
  <path d="m6.6 7.2 3.7 3.3" />
  <path d="m17.4 8.9-3.6 1.9" />
  <path d="m14.6 17.3-1.6-3.1" />
</>)

/** 匹配：人与岗位两端对齐，中间一条命中的连接 */
export const IMatch = line(<>
  <circle cx="12" cy="12" r="8.6" />
  <circle cx="12" cy="12" r="4.2" />
  <path d="M12 3.4V6" />
  <path d="M12 18v2.6" />
  <path d="M20.6 12H18" />
  <path d="M6 12H3.4" />
</>)

/** 人才：三人成组，中间一人在前（团队盘点） */
export const ITalent = line(<>
  <circle cx="12" cy="8.4" r="3" />
  <path d="M7.2 19.4a4.8 4.8 0 0 1 9.6 0" />
  <path d="M18.2 7.2a2.4 2.4 0 0 1 .6 4.6" />
  <path d="M19.4 18.2a4 4 0 0 0-1.7-2.7" />
  <path d="M5.8 7.2a2.4 2.4 0 0 0-.6 4.6" />
  <path d="M4.6 18.2a4 4 0 0 1 1.7-2.7" />
</>)

/** 审核：待办清单 + 勾选（候选/反馈审核） */
export const IReview = line(<>
  <path d="M9 4.2H7.2A1.8 1.8 0 0 0 5.4 6v13.2A1.8 1.8 0 0 0 7.2 21h9.6a1.8 1.8 0 0 0 1.8-1.8V6a1.8 1.8 0 0 0-1.8-1.8H15" />
  <rect x="9" y="2.6" width="6" height="3.2" rx="1.2" />
  <path d="m8.8 13.4 2.1 2.1 4.3-4.3" />
</>)

/** 重复/抄袭拦截：两份重叠文档 + 斜杠否决 */
export const IDuplicate = line(<>
  <path d="M8.6 8.6V5.4a1.8 1.8 0 0 1 1.8-1.8h8.2a1.8 1.8 0 0 1 1.8 1.8v8.2a1.8 1.8 0 0 1-1.8 1.8h-3.2" />
  <rect x="3.6" y="8.6" width="11.8" height="11.8" rx="1.8" />
  <path d="m6.6 17.4 5.8-5.8" />
</>)

/** 语料库：分层数据集 */
export const ICorpus = line(<>
  <ellipse cx="12" cy="5.8" rx="7.6" ry="2.8" />
  <path d="M4.4 5.8v6.4c0 1.55 3.4 2.8 7.6 2.8s7.6-1.25 7.6-2.8V5.8" />
  <path d="M4.4 12.2v6c0 1.55 3.4 2.8 7.6 2.8s7.6-1.25 7.6-2.8v-6" />
</>)

/** 覆盖率：环形进度缺口 + 勾 */
export const ICoverage = line(<>
  <path d="M20.4 12a8.4 8.4 0 1 1-4.2-7.27" />
  <path d="m8.6 11.8 2.6 2.6 6-6.4" />
</>)

/** 学习路径：路线节点串联 */
export const IRoute = line(<>
  <circle cx="6" cy="18.4" r="2.2" />
  <circle cx="18" cy="5.6" r="2.2" />
  <path d="M18 7.8v2.6a3 3 0 0 1-3 3H9a3 3 0 0 0-3 3v0" />
  <path d="M10.4 13.4h3.2" />
</>)

/** 证据溯源：带链接的凭证 */
export const IEvidence = line(<>
  <path d="M12 2.8 4.6 5.6v6c0 4.4 3 8.2 7.4 9.6 4.4-1.4 7.4-5.2 7.4-9.6v-6L12 2.8Z" />
  <path d="M10.4 12.6a2 2 0 0 1 0-2.8l1-1a2 2 0 0 1 2.8 2.8l-.5.5" />
  <path d="M13.6 11.4a2 2 0 0 1 0 2.8l-1 1a2 2 0 0 1-2.8-2.8l.5-.5" />
</>)
