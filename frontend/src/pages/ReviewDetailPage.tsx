import { AlertTriangle, ArrowLeft, Check, CheckCircle2, Clock3, Code2, ExternalLink, FileCode2, GitCommitHorizontal, GitMerge, GitPullRequest, MessageSquareText, PlayCircle, RefreshCw, ShieldCheck, TerminalSquare, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from '../router'
import { api } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import type { DiffFile, ReviewDetail } from '../types'

type Tab = 'overview' | 'diff' | 'evidence' | 'timeline'

export function ReviewDetailPage() {
  const { id } = useParams()
  const [review, setReview] = useState<ReviewDetail | null>(null)
  const [diff, setDiff] = useState<DiffFile[]>([])
  const [tab, setTab] = useState<Tab>('overview')
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    const value = await api.get<ReviewDetail>(`/api/reviews/${id}`)
    setReview(value)
    if (value.pr_number) api.get<{ files: DiffFile[] }>(`/api/reviews/${id}/diff`).then((result) => setDiff(result.files)).catch(() => setDiff([]))
  }
  useEffect(() => { load() }, [id])
  useEffect(() => {
    if (!review || !['QUEUED', 'PROCESSING', 'CHANGES_REQUESTED', 'APPLYING_FEEDBACK'].includes(review.status)) return
    const timer = window.setInterval(load, 4000)
    return () => window.clearInterval(timer)
  }, [review?.status])

  const accept = async () => {
    setBusy(true)
    try { await api.post(`/api/reviews/${id}/accept`, { confirmation: 'ACCEPT' }); setNotice('Changes accepted. Merge is now available.'); await load() } finally { setBusy(false) }
  }
  const requestChanges = async () => {
    setBusy(true)
    try { await api.post(`/api/reviews/${id}/feedback`, { body: feedback }); setFeedbackOpen(false); setFeedback(''); setNotice('Feedback posted to the PR. VisionPR will revise the same branch.'); await load() } finally { setBusy(false) }
  }
  const merge = async () => {
    setBusy(true)
    try { await api.post(`/api/reviews/${id}/merge`, { confirmation: 'MERGE', method: 'squash' }); setMergeOpen(false); setNotice('Pull request merged.'); await load() } finally { setBusy(false) }
  }
  const retry = async () => {
    setBusy(true)
    try { await api.post(`/api/reviews/${id}/start`); setNotice('Review queued again with the existing recording.'); await load() } finally { setBusy(false) }
  }

  if (!review) return <div className="page loading-page"><span className="spinner dark" /> Loading review</div>
  const processing = ['QUEUED', 'PROCESSING', 'CHANGES_REQUESTED', 'APPLYING_FEEDBACK'].includes(review.status)
  const noActionableTasks = review.status === 'NO_ACTIONABLE_TASKS'
  const failed = ['ERROR', 'REVIEW_FAILED', 'BUILD_FAILED', 'INCOMPLETE'].includes(review.status)
  const canAccept = ['AWAITING_HUMAN_REVIEW', 'PR_OPENED'].includes(review.status)
  const accepted = review.status === 'ACCEPTED'

  return (
    <div className="page review-detail-page">
      <header className="review-header">
        <div className="review-title-row">
          <Link className="back-link" to="/reviews"><ArrowLeft size={15} /> Reviews</Link>
          <div className="review-title"><span className="section-label">{review.repository}</span><h1>{review.title}</h1><div className="review-meta"><StatusBadge status={review.status} />{review.pr_number && <span><GitPullRequest size={14} /> PR #{review.pr_number}</span>}{review.commit_sha && <span><GitCommitHorizontal size={14} /> {review.commit_sha.slice(0, 8)}</span>}</div></div>
        </div>
        <div className="review-actions">
          {review.pr_url && <a className="button button-quiet button-small" href={review.pr_url} target="_blank" rel="noreferrer">Open PR <ExternalLink size={15} /></a>}
          {canAccept && <button className="button button-quiet button-small" onClick={() => setFeedbackOpen(true)}><MessageSquareText size={16} /> Request changes</button>}
          {canAccept && <button className="button button-primary button-small" onClick={accept} disabled={busy}><Check size={16} /> Accept changes</button>}
          {accepted && <button className="button button-primary button-small" onClick={() => setMergeOpen(true)}><GitMerge size={16} /> Merge PR</button>}
          {failed && <button className="button button-primary button-small" onClick={retry} disabled={busy}><RefreshCw size={16} /> Retry review</button>}
        </div>
      </header>

      {notice && <div className="notice-banner"><CheckCircle2 size={17} /> {notice}<button onClick={() => setNotice('')} title="Dismiss"><X size={14} /></button></div>}
      {processing && <div className="processing-banner"><span className="processing-pulse"><RefreshCw size={18} /></span><span><strong>VisionPR is working through the evidence.</strong><small>Transcription, repository mapping, implementation, and validation continue in an isolated worker.</small></span><StatusBadge status={review.status} /></div>}
      {failed && <div className="error-banner"><AlertTriangle size={18} /><span><strong>No pull request was created.</strong><small>{review.error_message || 'The proposed patch did not pass validation. Retry to start again from a clean repository copy.'}</small></span></div>}
      {noActionableTasks && <div className="notice-banner"><CheckCircle2 size={17} /><span><strong>No repository changes requested.</strong> The recording was analyzed and its report is complete; no branch or pull request was created.</span></div>}

      <nav className="detail-tabs" aria-label="Review detail views">
        {(['overview', 'diff', 'evidence', 'timeline'] as Tab[]).map((item) => <button className={tab === item ? 'active' : ''} onClick={() => setTab(item)} key={item}>{item}</button>)}
      </nav>

      {tab === 'overview' && <div className="detail-grid">
        <section className="content-panel task-panel">
          <div className="panel-heading"><div>Task queue</div><span>{review.tasks.length} task{review.tasks.length === 1 ? '' : 's'}</span></div>
          <div className="task-list">
            {review.tasks.map((task) => <article className="task-item" key={task.id}><span className="task-number">{String(task.task_number).padStart(2, '0')}</span><div><strong>{task.title}</strong><small>{task.timestamp ? `${Math.floor(task.timestamp / 60)}:${String(Math.round(task.timestamp % 60)).padStart(2, '0')} in recording` : 'Meeting evidence attached'}</small><div className="file-chips">{task.changed_files.map((file) => <span key={file}><FileCode2 size={12} /> {file}</span>)}</div></div><StatusBadge status={task.status} /></article>)}
            {!review.tasks.length && <div className="empty-state"><PlayCircle size={25} /><strong>{noActionableTasks ? 'No actionable tasks found' : 'Tasks are being prepared'}</strong><span>{noActionableTasks ? 'The recording did not contain an explicit request to change the repository.' : 'They will appear here as soon as the transcript is mapped.'}</span></div>}
          </div>
        </section>
        <aside className="review-side-stack">
          <section className="content-panel verification-card"><div className="panel-heading"><div>Verification</div></div><div className="verification-row"><span className="verification-icon"><TerminalSquare size={18} /></span><span><strong>Build</strong><small>Repository validation</small></span><StatusBadge status={review.build_status || (processing ? 'pending' : 'not applicable')} /></div><div className="verification-row"><span className="verification-icon teal"><ShieldCheck size={18} /></span><span><strong>Safety gate</strong><small>Git diff + protected paths</small></span><StatusBadge status={review.pr_number ? 'passed' : failed ? 'rejected' : noActionableTasks ? 'not applicable' : 'pending'} /></div></section>
          <section className="content-panel branch-card"><div className="panel-heading"><div>Branch</div></div><code>{review.head_branch || 'Created after implementation'}</code><small>main remains unchanged until merge.</small></section>
        </aside>
      </div>}

      {tab === 'diff' && <section className="content-panel diff-panel">
        <div className="panel-heading"><div>Files changed</div><span>{diff.length || review.changed_files.length} files</span></div>
        {diff.map((file) => <article className="diff-file" key={file.filename}><header><span><FileCode2 size={15} /> {file.filename}</span><span className="diff-count"><i>+{file.additions}</i><b>-{file.deletions}</b></span></header><pre>{file.patch}</pre></article>)}
        {!diff.length && <div className="empty-state"><Code2 size={26} /><strong>Diff will appear when the PR opens</strong><span>The complete patch always remains available on GitHub.</span></div>}
      </section>}

      {tab === 'evidence' && <section className="content-panel evidence-panel"><div className="panel-heading"><div>Meeting evidence</div></div>{review.tasks.map((task) => <article className="evidence-item" key={task.id}><span className="timestamp"><Clock3 size={14} /> {task.timestamp ? `${task.timestamp}s` : 'Context'}</span><blockquote>{task.transcript || 'The extracted transcript will appear with this task.'}</blockquote><strong>{task.title}</strong></article>)}</section>}

      {tab === 'timeline' && <section className="content-panel timeline-panel"><div className="panel-heading"><div>Activity</div></div>{review.events.map((event) => <article className="event-row" key={event.id}><span className="event-node" /><div><strong>{event.message}</strong><small>{new Date(event.created_at).toLocaleString()}</small></div></article>)}</section>}

      {feedbackOpen && <div className="modal-backdrop" role="presentation"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title"><button className="modal-close" onClick={() => setFeedbackOpen(false)} title="Close"><X size={17} /></button><span className="modal-icon purple"><MessageSquareText size={22} /></span><h2 id="feedback-title">Request changes</h2><p>Your feedback is posted to the current PR and becomes the next focused revision task.</p><label className="field-label"><span>What should change?</span><textarea rows={6} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="Keep the dependency note, but move it into a requirements file..." /></label><div className="modal-actions"><button className="button button-quiet" onClick={() => setFeedbackOpen(false)}>Cancel</button><button className="button button-primary" disabled={feedback.trim().length < 3 || busy} onClick={requestChanges}>Send feedback</button></div></div></div>}

      {mergeOpen && <div className="modal-backdrop" role="presentation"><div className="modal merge-modal" role="alertdialog" aria-modal="true" aria-labelledby="merge-title"><button className="modal-close" onClick={() => setMergeOpen(false)} title="Close"><X size={17} /></button><span className="modal-icon"><GitMerge size={22} /></span><h2 id="merge-title">Merge pull request?</h2><p>This is the final repository action. VisionPR will squash the accepted changes into <code>{review.default_branch || 'main'}</code>.</p><div className="merge-check"><CheckCircle2 size={17} /> Changes accepted by a human</div><div className="merge-check"><ShieldCheck size={17} /> Validation gates passed</div><div className="modal-actions"><button className="button button-quiet" onClick={() => setMergeOpen(false)}>Keep open</button><button className="button button-primary" disabled={busy} onClick={merge}>Confirm merge</button></div></div></div>}
    </div>
  )
}
