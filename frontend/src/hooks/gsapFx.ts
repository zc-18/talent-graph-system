import { useRef } from 'react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { useGSAP } from '@gsap/react'

gsap.registerPlugin(useGSAP, ScrollTrigger)

// 尊重系统"减少动态效果"：所有动效只在 no-preference 分支注册，
// reduce 用户直接看到最终态（JSX 里渲染的就是终值/终位）
const MOTION_OK = '(prefers-reduced-motion: no-preference)'

/** KPI 数字滚动：span 里正常渲染终值，动效从 0 滚到终值（transform 之外仅改 textContent） */
export function useCountUp<T extends HTMLElement = HTMLSpanElement>(
  value: number, { duration = 1.1, decimals = 0, suffix = '' } = {},
) {
  const ref = useRef<T>(null)
  useGSAP(() => {
    const el = ref.current
    if (!el) return
    const mm = gsap.matchMedia()
    mm.add(MOTION_OK, () => {
      const obj = { v: 0 }
      gsap.to(obj, {
        v: value, duration, ease: 'power2.out',
        onUpdate: () => { el.textContent = obj.v.toFixed(decimals) + suffix },
      })
    })
  }, { dependencies: [value], revertOnUpdate: true })
  return ref
}

/**
 * 子元素依次浮现。返回容器 ref，容器内所有匹配 selector 的元素：
 * - scroll=false：挂载后 timeline stagger 依次入场
 * - scroll=true：各自滚动进入视口时入场（ScrollTrigger，once）
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(
  selector = '[data-reveal]',
  { stagger = 0.08, y = 22, scroll = false, deps = [] as unknown[] } = {},
) {
  const ref = useRef<T>(null)
  useGSAP(() => {
    const container = ref.current
    if (!container) return
    const mm = gsap.matchMedia()
    mm.add(MOTION_OK, () => {
      const els = gsap.utils.toArray<HTMLElement>(selector, container)
      if (!els.length) return
      if (scroll) {
        els.forEach(el => gsap.from(el, {
          opacity: 0, y, duration: 0.55, ease: 'power2.out',
          scrollTrigger: { trigger: el, start: 'top 90%', once: true },
        }))
      } else {
        gsap.from(els, { opacity: 0, y, duration: 0.55, ease: 'power2.out', stagger })
      }
    })
  }, { dependencies: deps, revertOnUpdate: true })
  return ref
}

/** 装饰元素慢速漂浮（transform-only，无限 yoyo） */
export function useFloat<T extends HTMLElement = HTMLDivElement>(
  { y = 10, duration = 3, rotate = 0, deps = [] as unknown[] } = {},
) {
  const ref = useRef<T>(null)
  useGSAP(() => {
    const el = ref.current
    if (!el) return
    const mm = gsap.matchMedia()
    mm.add(MOTION_OK, () => {
      gsap.to(el, { y: -y, rotation: rotate, duration, repeat: -1, yoyo: true, ease: 'sine.inOut' })
    })
  }, { dependencies: deps, revertOnUpdate: true })
  return ref
}

/** 鼠标视差：容器内移动鼠标时，目标层用 quickTo 轻微跟随（用于图谱背景等大面积底图） */
export function useMouseParallax<T extends HTMLElement = HTMLDivElement>(
  targetSelector: string, { strength = 14 } = {},
) {
  const ref = useRef<T>(null)
  useGSAP((_ctx, contextSafe) => {
    const container = ref.current
    if (!container || !contextSafe) return
    const mm = gsap.matchMedia()
    mm.add(MOTION_OK, () => {
      const target = container.querySelector<HTMLElement>(targetSelector)
      if (!target) return
      const toX = gsap.quickTo(target, 'x', { duration: 0.6, ease: 'power3.out' })
      const toY = gsap.quickTo(target, 'y', { duration: 0.6, ease: 'power3.out' })
      const onMove = contextSafe((e: MouseEvent) => {
        const r = container.getBoundingClientRect()
        toX(((e.clientX - r.left) / r.width - 0.5) * -strength)
        toY(((e.clientY - r.top) / r.height - 0.5) * -strength)
      }) as (e: MouseEvent) => void
      const onLeave = contextSafe(() => { toX(0); toY(0) }) as () => void
      container.addEventListener('mousemove', onMove)
      container.addEventListener('mouseleave', onLeave)
      return () => {
        container.removeEventListener('mousemove', onMove)
        container.removeEventListener('mouseleave', onLeave)
      }
    })
  })
  return ref
}

/** 学习路径/时间线竖线随滚动生长（scaleY 0→1） */
export function useGrowLine<T extends HTMLElement = HTMLDivElement>(lineSelector: string) {
  const ref = useRef<T>(null)
  useGSAP(() => {
    const mm = gsap.matchMedia()
    mm.add(MOTION_OK, () => {
      const line = ref.current?.querySelector<HTMLElement>(lineSelector)
      if (!line) return
      gsap.from(line, {
        scaleY: 0, transformOrigin: 'top center', ease: 'none',
        scrollTrigger: { trigger: ref.current, start: 'top 85%', end: 'bottom 60%', scrub: 0.4 },
      })
    })
  })
  return ref
}
