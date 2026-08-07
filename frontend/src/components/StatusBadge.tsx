const normalize = (value: string) => value.toLowerCase().replaceAll('_', '-')

export function StatusBadge({ status }: { status: string }) {
  const label = status.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase())
  return <span className={`status-badge status-${normalize(status)}`}>{label}</span>
}
