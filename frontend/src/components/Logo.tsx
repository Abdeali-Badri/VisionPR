import { Code2 } from 'lucide-react'
import { Link } from '../router'

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <Link className={`brand ${compact ? 'brand-compact' : ''}`} to="/" aria-label="VisionPR home">
      <span className="brand-mark" aria-hidden="true"><Code2 size={compact ? 17 : 21} /></span>
      {!compact && <span>Vision<span className="brand-red">PR</span></span>}
    </Link>
  )
}
