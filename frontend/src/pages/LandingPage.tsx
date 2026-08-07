import { ArrowRight, Blocks, Bot, Code2, ExternalLink, Github, LayoutGrid, LogIn, LogOut, ShieldCheck, Sparkles, Users } from 'lucide-react'
import { Link } from '../router'
import { Logo } from '../components/Logo'
import { PixelRidge } from '../components/PixelRidge'
import type { User } from '../types'

const features = [
  { title: 'AI Implementation', copy: 'Reads the evidence and maps precise code changes.', icon: Sparkles, tone: 'red' },
  { title: 'Smart Pull Requests', copy: 'Small diffs, build proof, and review-ready context.', icon: Code2, tone: 'blue' },
  { title: 'Human in Control', copy: 'Accept, refine, or stop every task before merge.', icon: Users, tone: 'purple' },
  { title: 'Secure by Design', copy: 'Tokens stay server-side and main stays untouched.', icon: ShieldCheck, tone: 'teal' },
]

export function LandingPage({ authenticated = false, user, onLogout }: { authenticated?: boolean; user?: User | null; onLogout?: () => Promise<void> }) {
  return (
    <div className="landing-shell">
      <section className="landing-panel">
        <header className="landing-nav">
          <Logo />
          <nav aria-label="Main navigation">
            <a href="#workflow">How it works</a>
            <a href="#trust">Trust</a>
            <a href="/docs" onClick={(event) => event.preventDefault()}>Docs</a>
          </nav>
          {authenticated ? (
            <div className="nav-auth-actions">
              <span className="nav-account">@{user?.login || 'github'}</span>
              <Link className="button button-quiet nav-dashboard" to="/dashboard"><LayoutGrid size={16} /> Dashboard</Link>
              <button className="button button-quiet nav-signout" onClick={onLogout}><LogOut size={16} /> Sign out</button>
            </div>
          ) : <a className="button button-quiet nav-signin" href="/api/auth/github/start"><LogIn size={17} /> Sign in</a>}
        </header>

        <div className="hero-copy">
          <span className="hero-kicker">AI-POWERED <i /> HUMAN-TRUSTED</span>
          <h1>Vision<span>PR</span></h1>
          <p>From meeting to merged PR, with the evidence attached.</p>
          <small>Turn spoken feedback into tested code changes your team can review.</small>
          <div className="hero-actions">
            <Link className="button button-primary" to="/reviews/new"><ArrowRight size={18} /> Start Review</Link>
            <Link className="button button-quiet" to="/dashboard"><LayoutGrid size={18} /> Open Dashboard</Link>
          </div>
        </div>

        <div className="feature-row" id="trust">
          {features.map(({ title, copy, icon: Icon, tone }) => (
            <article className="feature-item" key={title}>
              <span className={`feature-icon tone-${tone}`}><Icon size={21} /></span>
              <span><strong>{title}</strong><small>{copy}</small></span>
            </article>
          ))}
        </div>
        <PixelRidge />
      </section>

      <section className="workflow-band" id="workflow">
        <div className="workflow-heading">
          <span className="section-label">THE REVIEW LOOP</span>
          <h2>A clean handoff at every decision.</h2>
          <p>VisionPR moves only when the evidence, code, and human verdict agree.</p>
        </div>
        <div className="pixel-timeline">
          {[
            ['01', 'Capture', 'Recording or YouTube'],
            ['02', 'Understand', 'Transcript + repository'],
            ['03', 'Implement', 'Patch + validation'],
            ['04', 'Review', 'Accept or request changes'],
            ['05', 'Merge', 'Separate confirmation'],
          ].map(([number, title, copy], index) => (
            <div className="timeline-step" key={number}>
              <span className={index === 3 ? 'timeline-node active' : 'timeline-node'}>{number}</span>
              <strong>{title}</strong><small>{copy}</small>
            </div>
          ))}
        </div>
        <div className="workflow-footer">
          <span><Github size={18} /> GitHub-native review</span>
          <span><Bot size={18} /> Provider-adapter agents</span>
          <span><Blocks size={18} /> Framework-aware validation</span>
          <Link to="/reviews/new">Create a review <ExternalLink size={15} /></Link>
        </div>
      </section>
    </div>
  )
}
