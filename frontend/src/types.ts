export type User = {
  id: number
  login: string
  display_name: string
  avatar_url?: string | null
}

export type Project = {
  id: number
  repository: string
  repository_url: string
  default_branch: string
  language?: string
  status: string
  updated_at: string
}

export type ReviewSummary = {
  id: number
  run_id: string
  title: string
  status: string
  repository: string
  pr_url?: string | null
  pr_number?: number | null
  updated_at: string
  build_status?: string | null
}

export type ReviewTask = {
  id: number
  task_number: number
  title: string
  timestamp?: number | null
  transcript?: string | null
  screenshot_path?: string | null
  status: string
  feedback?: string | null
  changed_files: string[]
}

export type TimelineEvent = {
  id: number
  event_type: string
  message: string
  created_at: string
  metadata: Record<string, unknown>
}

export type ReviewDetail = ReviewSummary & {
  default_branch?: string
  source_type: string
  source_value?: string | null
  current_step: number
  head_branch?: string | null
  commit_sha?: string | null
  changed_files: string[]
  error_message?: string | null
  tasks: ReviewTask[]
  events: TimelineEvent[]
}

export type DashboardData = {
  stats: {
    projects: number
    reviews: number
    pull_requests: number
    merged: number
    merge_rate: number
  }
  recent_reviews: ReviewSummary[]
  recent_projects: Project[]
}

export type DiffFile = {
  filename: string
  status: string
  additions: number
  deletions: number
  changes: number
  patch: string
}
