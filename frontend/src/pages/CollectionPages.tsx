import { ArrowRight, Boxes, CheckCircle2, ExternalLink, FolderGit2, Github, GitPullRequest, Search, Settings2, Slack } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from '../router'
import { api } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import type { Project, ReviewSummary } from '../types'

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  useEffect(() => { api.get<Project[]>('/api/projects').then(setProjects) }, [])
  return <Collection title="Projects" subtitle="Repositories VisionPR can map, edit, and validate."><div className="collection-grid">{projects.map((project) => <article className="project-card" key={project.id}><span className="project-card-icon"><FolderGit2 size={21} /></span><div><span className="section-label">{project.language || project.default_branch}</span><h3>{project.repository}</h3><p>Default branch: {project.default_branch}</p></div><a href={project.repository_url} target="_blank" rel="noreferrer" className="icon-button" title="Open repository"><ExternalLink size={16} /></a></article>)}</div></Collection>
}

export function ReviewsPage({ pullRequestsOnly = false }: { pullRequestsOnly?: boolean }) {
  const [reviews, setReviews] = useState<ReviewSummary[]>([])
  useEffect(() => { api.get<ReviewSummary[]>('/api/reviews').then((items) => setReviews(pullRequestsOnly ? items.filter((item) => item.pr_number) : items)) }, [pullRequestsOnly])
return <Collection title={pullRequestsOnly ? 'Pull Requests' : 'Reviews'} subtitle={pullRequestsOnly ? 'Every branch VisionPR has prepared for human judgment.' : 'Meeting evidence, implementation status, and human decisions.'}><div className="table-toolbar"><div className="search-box"><Search size={16} /><input placeholder="Search reviews" /></div><span>{reviews.length} total</span></div><div className="review-table">{reviews.map((review) => <Link to={`/reviews/${review.id}`} className="review-table-row" key={review.id}><span className="row-icon"><GitPullRequest size={16} /></span><span className="row-main"><strong>{review.title}</strong><small>{review.repository} / {review.run_id}</small></span>{review.pr_number && <span className="pr-number">#{review.pr_number}</span>}<StatusBadge status={review.status} /><ArrowRight size={15} /></Link>)}</div></Collection>
}

export function IntegrationsPage() {
  return <Collection title="Integrations" subtitle="Identity and delivery channels connected to this workspace."><div className="integration-grid"><Integration icon={Github} title="GitHub" description="OAuth, repositories, pull requests, and reviews." connected /><Integration icon={Slack} title="Slack" description="Send review-ready notifications to engineering channels." /><Integration icon={Boxes} title="Linear" description="Attach completed reports to product tasks." /></div></Collection>
}

export function SettingsPage() {
  return <Collection title="Settings" subtitle="Workspace-level safeguards for autonomous repository work."><section className="content-panel settings-list"><Setting title="Human merge confirmation" description="Always require a second action after accepting changes." enabled /><Setting title="Automatic framework detection" description="Choose validation commands from repository manifests." enabled /><Setting title="Review notifications" description="Notify reviewers when a PR or revision is ready." enabled /><Setting title="Allow direct pushes to main" description="VisionPR never writes directly to the default branch." /></section></Collection>
}

function Collection({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <div className="page collection-page"><header className="page-header"><div><span className="section-label">VISIONPR / WORKSPACE</span><h1>{title}</h1><p>{subtitle}</p></div><Link className="button button-primary button-small" to="/reviews/new">New Review <ArrowRight size={15} /></Link></header>{children}</div>
}

function Integration({ icon: Icon, title, description, connected = false }: { icon: typeof Github; title: string; description: string; connected?: boolean }) {
  return <article className="integration-card"><span className="integration-big-icon"><Icon size={25} /></span><div><h3>{title}</h3><p>{description}</p></div>{connected ? <StatusBadge status="connected" /> : <button className="button button-quiet button-small">Connect</button>}</article>
}

function Setting({ title, description, enabled = false }: { title: string; description: string; enabled?: boolean }) {
  return <div className="setting-row"><span className="setting-icon">{enabled ? <CheckCircle2 size={18} /> : <Settings2 size={18} />}</span><span><strong>{title}</strong><small>{description}</small></span><span className={`toggle ${enabled ? 'on' : ''}`}><i /></span></div>
}
