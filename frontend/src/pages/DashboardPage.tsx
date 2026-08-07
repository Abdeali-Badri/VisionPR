import { Activity, ArrowRight, CheckCircle2, FolderGit2, GitPullRequest, Plus, RadioTower } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from '../router'
import { api } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import type { DashboardData } from '../types'

const emptyData: DashboardData = { stats: { projects: 0, reviews: 0, pull_requests: 0, merged: 0, merge_rate: 0 }, recent_reviews: [], recent_projects: [] }

export function DashboardPage() {
  const [data, setData] = useState<DashboardData>(emptyData)
  const [loading, setLoading] = useState(true)
  useEffect(() => { api.get<DashboardData>('/api/dashboard').then(setData).finally(() => setLoading(false)) }, [])
  const stats = [
    ['Projects', data.stats.projects, FolderGit2, 'orange'],
    ['Reviews', data.stats.reviews, Activity, 'blue'],
    ['PRs created', data.stats.pull_requests, GitPullRequest, 'red'],
    ['Merge rate', `${data.stats.merge_rate}%`, CheckCircle2, 'teal'],
  ] as const
  return (
    <div className="page dashboard-page">
      <header className="page-header">
        <div><span className="section-label">WORKSPACE / TODAY</span><h1>Dashboard</h1><p>Everything waiting for your judgment, in one place.</p></div>
        <Link className="button button-primary button-small" to="/reviews/new"><Plus size={16} /> New Review</Link>
      </header>
      <div className="stats-grid" aria-busy={loading}>
        {stats.map(([label, value, Icon, tone]) => <article className="stat-cell" key={label}><span><strong>{value}</strong><small>{label}</small></span><Icon className={`text-${tone}`} size={22} /></article>)}
      </div>
      <div className="dashboard-grid">
        <section className="content-panel recent-reviews">
          <div className="panel-heading"><div><span className="live-dot" /> Recent Reviews</div><Link to="/reviews">View all <ArrowRight size={14} /></Link></div>
          <div className="review-list">
            {data.recent_reviews.map((review) => (
              <Link className="review-row" to={`/reviews/${review.id}`} key={review.id}>
                <span className="row-icon"><GitPullRequest size={15} /></span>
                <span className="row-main"><strong>{review.title}</strong><small>{review.repository} / {new Date(review.updated_at).toLocaleDateString()}</small></span>
                <StatusBadge status={review.status} />
              </Link>
            ))}
            {!loading && data.recent_reviews.length === 0 && <div className="empty-state"><RadioTower size={24} /><strong>No reviews yet</strong><span>Create a review from your latest engineering meeting.</span></div>}
          </div>
        </section>
        <aside className="dashboard-side">
          <section className="content-panel">
            <div className="panel-heading"><div>Recent Projects</div><Link to="/projects"><ArrowRight size={14} /></Link></div>
            <div className="project-list">
              {data.recent_projects.map((project) => <Link to="/projects" className="project-row" key={project.id}><FolderGit2 size={16} /><span><strong>{project.repository.split('/')[1]}</strong><small>{project.language || project.default_branch}</small></span></Link>)}
            </div>
          </section>
          <section className="content-panel integration-panel">
            <div className="panel-heading"><div>Active Integration</div></div>
            <div className="integration-row"><span className="github-disc"><GitPullRequest size={22} /></span><span><strong>GitHub</strong><small>Branches, reviews, and merges</small></span><StatusBadge status="connected" /></div>
          </section>
        </aside>
      </div>
    </div>
  )
}
