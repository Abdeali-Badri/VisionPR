import { AlertCircle, ArrowLeft, ArrowRight, Check, FileJson2, Film, Github, Link2, LockKeyhole, Play, ShieldCheck, UploadCloud, X } from 'lucide-react'
import { DragEvent, useRef, useState } from 'react'
import { Link, useNavigate } from '../router'
import { api } from '../api'
import type { ReviewDetail } from '../types'

const steps = ['Evidence', 'Repository', 'Guardrails', 'Launch']

export function NewReviewPage() {
  const [step, setStep] = useState(1)
  const [sourceType, setSourceType] = useState<'recording' | 'youtube' | 'intelligence'>('recording')
  const [file, setFile] = useState<File | null>(null)
  const [youtube, setYoutube] = useState('')
  const [title, setTitle] = useState('')
  const [repository, setRepository] = useState('')
  const [buildCommand, setBuildCommand] = useState('')
  const [constraint, setConstraint] = useState('Keep the patch focused on the meeting task.')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const chooseFile = (candidate?: File) => {
    if (!candidate) return
    setFile(candidate)
    setSourceType(candidate.name.endsWith('.json') ? 'intelligence' : 'recording')
  }
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]) }
  const canContinue = step === 1 ? (sourceType === 'youtube' ? youtube.includes('youtu') : Boolean(file)) : step === 2 ? title.length >= 3 && repository.includes('github.com/') : true

  const launch = async () => {
    setBusy(true); setError('')
    try {
      const review = await api.post<ReviewDetail>('/api/reviews', {
        title,
        repository_url: repository,
        source_type: sourceType,
        source_value: sourceType === 'youtube' ? youtube : null,
        build_commands: buildCommand.trim() ? [buildCommand.trim()] : [],
        constraints: constraint.trim() ? [constraint.trim()] : [],
      })
      if (file) await api.upload(`/api/reviews/${review.id}/upload`, file)
      await api.post(`/api/reviews/${review.id}/start`)
      navigate(`/reviews/${review.id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'VisionPR could not start this review.')
    } finally { setBusy(false) }
  }

  return (
    <div className="page wizard-page">
      <header className="wizard-topbar">
        <Link to="/dashboard"><ArrowLeft size={15} /> Back to dashboard</Link>
        <span className="step-count">STEP {step} OF 4</span>
        <div className="step-dots" aria-label={`Step ${step} of 4`}>{steps.map((label, index) => <span className={index + 1 <= step ? 'active' : ''} key={label}>{index + 1}</span>)}</div>
      </header>

      <div className="wizard-heading"><span className="section-label">NEW REVIEW / {steps[step - 1].toUpperCase()}</span><h1>{step === 1 ? 'Start with the meeting.' : step === 2 ? 'Choose the codebase.' : step === 3 ? 'Set the boundaries.' : 'Ready for the handoff.'}</h1><p>{step === 1 ? 'Upload the recording or use a YouTube link. VisionPR keeps every task tied to evidence.' : step === 2 ? 'VisionPR will clone this repository into an isolated workspace.' : step === 3 ? 'Use automatic validation or add a project-specific command.' : 'Review the access and actions before the worker starts.'}</p></div>

      <div className="wizard-body">
        {step === 1 && <div className="wizard-section">
          <div className="segmented-control" role="tablist">
            <button className={sourceType !== 'youtube' ? 'active' : ''} onClick={() => setSourceType('recording')}><Film size={16} /> Upload</button>
            <button className={sourceType === 'youtube' ? 'active' : ''} onClick={() => setSourceType('youtube')}><Link2 size={16} /> YouTube</button>
          </div>
          {sourceType !== 'youtube' ? <>
            <div className={`drop-zone ${file ? 'has-file' : ''}`} onDragOver={(event) => event.preventDefault()} onDrop={drop} onClick={() => inputRef.current?.click()}>
              <input ref={inputRef} type="file" hidden accept="video/*,.json" onChange={(event) => chooseFile(event.target.files?.[0])} />
              {file ? <><span className="upload-icon success"><Check size={22} /></span><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB / ready to upload</small><button className="file-remove" onClick={(event) => { event.stopPropagation(); setFile(null) }} title="Remove file"><X size={15} /></button></> : <><span className="upload-icon"><UploadCloud size={23} /></span><strong>Drag and drop your recording</strong><span>or click to browse</span><small>Video up to 500 MB / intelligence JSON also supported</small></>}
            </div>
            <div className="upload-notes"><span><Film size={14} /> Meeting recording</span><span><FileJson2 size={14} /> Existing intelligence JSON</span><span><ShieldCheck size={14} /> Stored only for this run</span></div>
          </> : <label className="field-label"><span>YouTube URL</span><div className="input-with-icon"><Link2 size={17} /><input value={youtube} onChange={(event) => setYoutube(event.target.value)} placeholder="https://youtu.be/..." /></div><small>Playlists are ignored; only the selected video is processed.</small></label>}
        </div>}

        {step === 2 && <div className="wizard-section field-stack">
          <label className="field-label"><span>Review title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Improve loading state in dashboard" maxLength={120} /></label>
          <label className="field-label"><span>GitHub repository</span><div className="input-with-icon"><Github size={17} /><input value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="https://github.com/owner/repository" /></div><small>Public repositories are forked automatically when direct push access is unavailable.</small></label>
          <div className="permission-note"><LockKeyhole size={18} /><span><strong>Main remains untouched.</strong><small>VisionPR creates a feature branch and never merges without a separate confirmation.</small></span></div>
        </div>}

        {step === 3 && <div className="wizard-section field-stack">
          <label className="field-label"><span>Validation command <em>optional</em></span><input value={buildCommand} onChange={(event) => setBuildCommand(event.target.value)} placeholder="Auto-detect from repository" /><small>Examples: python -m pytest, npm test, go test ./...</small></label>
          <label className="field-label"><span>Implementation constraint</span><textarea rows={4} value={constraint} onChange={(event) => setConstraint(event.target.value)} /></label>
          <div className="guardrail-grid"><span><Check /> Git diff verified</span><span><Check /> Protected paths blocked</span><span><Check /> Human merge gate</span><span><Check /> Token stays server-side</span></div>
        </div>}

        {step === 4 && <div className="wizard-section launch-summary">
          <div className="summary-row"><span>Evidence</span><strong>{sourceType === 'youtube' ? youtube : file?.name}</strong></div>
          <div className="summary-row"><span>Repository</span><strong>{repository}</strong></div>
          <div className="summary-row"><span>Validation</span><strong>{buildCommand || 'Automatic'}</strong></div>
          <div className="summary-row"><span>Merge policy</span><strong>Explicit confirmation only</strong></div>
          <div className="launch-consent"><ShieldCheck size={23} /><span><strong>VisionPR may create branches and pull requests in this repository.</strong><small>It cannot merge until you accept the task and confirm merge separately.</small></span></div>
        </div>}

        {error && <div className="error-banner"><AlertCircle size={17} /> {error}</div>}
        <div className="wizard-actions">
          <button className="button button-quiet" disabled={step === 1 || busy} onClick={() => setStep((value) => value - 1)}><ArrowLeft size={16} /> Back</button>
          {step < 4 ? <button className="button button-primary" disabled={!canContinue} onClick={() => setStep((value) => value + 1)}>Continue <ArrowRight size={16} /></button> : <button className="button button-primary" disabled={busy} onClick={launch}>{busy ? <span className="spinner" /> : <Play size={16} />} {busy ? 'Starting worker' : 'Start review'}</button>}
        </div>
      </div>
    </div>
  )
}
