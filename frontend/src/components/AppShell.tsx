import { BookOpen, Boxes, FolderGit2, GitPullRequest, LayoutDashboard, LogOut, Plus, Settings, SlidersHorizontal } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink } from '../router'
import type { User } from '../types'
import { Logo } from './Logo'

const nav = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/projects', label: 'Projects', icon: FolderGit2 },
  { to: '/reviews', label: 'Reviews', icon: BookOpen },
  { to: '/pull-requests', label: 'Pull Requests', icon: GitPullRequest },
  { to: '/integrations', label: 'Integrations', icon: Boxes },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export function AppShell({ children, user, onLogout }: { children: ReactNode; user?: User | null; onLogout: () => Promise<void> }) {
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="sidebar-top"><Logo compact /></div>
        <NavLink className="new-review-button" to="/reviews/new"><Plus size={17} /> New Review</NavLink>
        <nav className="side-nav" aria-label="Workspace">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'side-link active' : 'side-link'}>
              <Icon size={16} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-tile">
            <span className="avatar">{user?.avatar_url ? <img src={user.avatar_url} alt="" /> : (user?.display_name || 'G').slice(0, 1)}</span>
            <span className="user-copy"><strong>{user?.display_name || 'GitHub user'}</strong><small>@{user?.login || 'connected'}</small></span>
            <button className="signout-button" onClick={onLogout}><LogOut size={15} /> Sign out</button>
          </div>
        </div>
      </aside>
      <main className="workspace">
        <div className="mobile-bar"><Logo /><div className="mobile-actions"><NavLink className="icon-button" to="/reviews/new" title="New review"><Plus size={18} /></NavLink><button className="mobile-signout" onClick={onLogout}><LogOut size={15} /> Sign out</button></div></div>
        {children}
      </main>
    </div>
  )
}
