import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from './router'
import { api } from './api'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { IntegrationsPage, ProjectsPage, ReviewsPage, SettingsPage } from './pages/CollectionPages'
import { LandingPage } from './pages/LandingPage'
import { NewReviewPage } from './pages/NewReviewPage'
import { ReviewDetailPage } from './pages/ReviewDetailPage'
import type { User } from './types'

export function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const [user, setUser] = useState<User | null>(null)
  const [authReady, setAuthReady] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [authMode, setAuthMode] = useState<'github' | 'demo'>('github')
  useEffect(() => {
    setAuthReady(false)
    api.get<{ authenticated: boolean; user: User | null; mode: 'github' | 'demo' }>('/api/auth/me')
      .then((value) => { setUser(value.user); setAuthenticated(value.authenticated); setAuthMode(value.mode) })
      .catch(() => { setUser(null); setAuthenticated(false); setAuthMode('github') })
      .finally(() => setAuthReady(true))
  }, [location.pathname])
  const logout = async () => {
    await api.post('/api/auth/logout')
    setUser(null)
    setAuthenticated(false)
    setAuthMode('github')
    navigate('/')
  }
  const landing = location.pathname === '/'
  if (landing) return <LandingPage authenticated={authenticated} user={user} onLogout={logout} />
  if (!authReady) return <div className="page loading-page"><span className="spinner dark" /> Checking GitHub session</div>
  if (!authenticated && authMode !== 'demo') return <Navigate to="/" replace />
  return (
    <AppShell user={user} onLogout={logout}>
      <Routes>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/reviews" element={<ReviewsPage />} />
        <Route path="/reviews/new" element={<NewReviewPage />} />
        <Route path="/reviews/:id" element={<ReviewDetailPage />} />
        <Route path="/pull-requests" element={<ReviewsPage pullRequestsOnly />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
  )
}
