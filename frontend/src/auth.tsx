import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { AUTH_UNAUTHORIZED_EVENT, api, AppRole, AuthResponse, AuthUser, setAccessToken } from './api'
import { clearSessionToken, readSessionToken, writeSessionToken } from './authStorage'
import { Spinner } from './components/ui'

type AuthContextValue = {
  user: AuthUser | null
  ready: boolean
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<AuthUser>
  register: (body: { username: string; password: string; role?: 'user' | 'hr'; organization_name?: string }) => Promise<AuthUser>
  logout: () => Promise<void>
  can: (...roles: AppRole[]) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [ready, setReady] = useState(false)

  const clear = useCallback(async () => {
    setAccessToken(null)
    setUser(null)
    await clearSessionToken()
  }, [])

  const accept = useCallback(async (result: AuthResponse) => {
    setAccessToken(result.access_token)
    await writeSessionToken(result.access_token)
    setUser(result.user)
    return result.user
  }, [])

  useEffect(() => {
    let active = true
    readSessionToken().then(async token => {
      if (!token) return
      setAccessToken(token)
      try {
        const me = await api.me()
        if (active) setUser(me)
      } catch {
        await clear()
      }
    }).finally(() => active && setReady(true))
    const expired = () => { void clear() }
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, expired)
    return () => { active = false; window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, expired) }
  }, [clear])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    ready,
    isAuthenticated: !!user,
    login: async (username, password) => accept(await api.login({ username, password })),
    register: async body => accept(await api.register(body)),
    logout: async () => {
      try { if (user) await api.logout() } finally { await clear() }
    },
    can: (...roles) => !!user && roles.includes(user.role),
  }), [accept, clear, ready, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}

export function RequireAuth({ children, roles }: { children: ReactNode; roles?: AppRole[] }) {
  const auth = useAuth()
  const location = useLocation()
  const redirectState = useMemo(() => ({ from: location.pathname }), [location.pathname])
  if (!auth.ready) return <Spinner label="正在恢复会话…" />
  if (!auth.user) return <Navigate to="/login" replace state={redirectState} />
  if (roles && !roles.includes(auth.user.role)) return <Navigate to="/" replace />
  return <>{children}</>
}
