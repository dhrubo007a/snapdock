// Shared TypeScript types matching backend Pydantic schemas

export interface ContainerSummary {
  id: string
  name: string
  status: string
  image: string
}

export interface StackResponse {
  name: string
  type: string
  health_state: 'CLEAN' | 'DEGRADED' | 'BROKEN'
  containers: ContainerSummary[]
  has_schedule: boolean
  snapshot_protected: boolean
  last_snapshot_at: string | null
  last_verified_at: string | null
  coupled_stacks: string[]
}

export interface SnapshotResponse {
  id: string
  stack_name: string
  stack_type: string
  stack_state: string
  label: string | null
  tags: string[]
  locked: boolean
  trigger_type: string
  triggered_by: string
  generated_at: string
  finalized_at: string | null
  complete: boolean
  size_bytes: number | null
  verified: boolean
  verified_at: string | null
}

export interface CoverageRow {
  stack_name: string
  last_clean_snap_at: string | null
  schedule_cron: string | null
  last_verified_at: string | null
  status: 'covered' | 'overdue' | 'unprotected'
}

export interface CoverageDashboard {
  rows: CoverageRow[]
  total: number
  protected: number
  overdue: number
  unprotected: number
}

export interface SnapDockEvent {
  event_type: string
  stack_name?: string
  snapshot_id?: string
  step?: number
  status?: string
  message?: string
  timestamp?: string
}
