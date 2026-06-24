import { useState } from 'react'
import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import {
  LayoutDashboard, Network, Briefcase, Sparkles, GitBranch, Target, ChevronLeft, ChevronRight,
} from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Panorama from './pages/Panorama'
import Jobs from './pages/Jobs'
import JobDetail from './pages/JobDetail'
import Discovery from './pages/Discovery'
import Evolution from './pages/Evolution'
import Match from './pages/Match'
import ChatBot from './components/ChatBot'

const NAV = [
  { to: '/', label: '数据驾驶舱', icon: LayoutDashboard, end: true },
  { to: '/panorama', label: '全景能力图谱', icon: Network },
  { to: '/discovery', label: '新岗位发现', icon: Sparkles },
  { to: '/evolution', label: '岗位能力演化', icon: GitBranch },
  { to: '/jobs', label: '岗位库管理', icon: Briefcase },
  { to: '/match', label: '人岗匹配诊断', icon: Target },
]

function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside className={`${collapsed ? 'w-[78px]' : 'w-64'} shrink-0 h-screen sticky top-0 z-30 flex flex-col px-3 py-6 border-r border-slate-200/70 bg-white/55 backdrop-blur-xl transition-[width] duration-300 ease-in-out relative`}>
      {/* 浮于侧栏右边缘的收起/展开按钮 */}
      <button onClick={onToggle} title={collapsed ? '展开侧栏' : '收起侧栏'}
        className="absolute -right-3 top-8 z-40 w-6 h-6 rounded-full bg-white border border-slate-200 shadow-md grid place-items-center text-slate-400 hover:text-accent hover:border-accent/50 hover:scale-110 transition">
        {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
      </button>

      <div className={`flex items-center gap-3 mb-8 ${collapsed ? 'justify-center px-0' : 'px-2'}`}>
        <div className="w-10 h-10 rounded-xl bg-white grid place-items-center shadow-card border border-slate-100 shrink-0 overflow-hidden">
          <img src="/logo.png" alt="智岗图谱" className="w-full h-full object-cover" />
        </div>
        {!collapsed && (
          <div className="overflow-hidden whitespace-nowrap">
            <div className="font-extrabold text-lg leading-tight gradient-text">智岗图谱</div>
            <div className="text-[11px] text-slate-500 tracking-wide">TalentGraph AI</div>
          </div>
        )}
      </div>

      <nav className="flex flex-col gap-1">
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end} title={collapsed ? n.label : undefined}
            className={({ isActive }) =>
              `group relative flex items-center rounded-xl text-sm font-medium transition-all ${collapsed ? 'justify-center px-0 py-3' : 'gap-3 px-3.5 py-2.5'} ${
                isActive ? 'bg-grad-accent text-white shadow-glow'
                         : 'text-slate-500 hover:text-slate-800 hover:bg-white/70'}`}>
            <n.icon className="w-[18px] h-[18px] shrink-0" />
            {!collapsed && <span className="whitespace-nowrap">{n.label}</span>}
          </NavLink>
        ))}
      </nav>

      {!collapsed && (
        <div className="mt-auto glass p-3.5 text-[11px] text-slate-500 leading-relaxed">
          <div className="font-semibold text-slate-700 mb-1">多源 · 反幻觉 · 动态演化</div>
          数据驱动 + 大模型 + 知识图谱<br />构建可自我进化的人才能力大脑
        </div>
      )}
    </aside>
  )
}

export default function App() {
  const loc = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div className="flex min-h-screen">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(c => !c)} />
      <main className="flex-1 min-w-0 px-6 lg:px-8 py-7">
        <div className="max-w-[1400px] mx-auto">
          <AnimatePresence mode="wait">
            <motion.div key={loc.pathname}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.28 }}>
              <Routes location={loc}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/panorama" element={<Panorama />} />
                <Route path="/discovery" element={<Discovery />} />
                <Route path="/evolution" element={<Evolution />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/jobs/:id" element={<JobDetail />} />
                <Route path="/match" element={<Match />} />
              </Routes>
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
      <ChatBot />
    </div>
  )
}
