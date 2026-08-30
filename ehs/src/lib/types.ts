export interface Deadline {
  id: string
  kind: string
  regulation_ref: string | null
  clock_type: string
  starts_at: string
  due_at: string
  satisfied_at: string | null
  escalation_level: number
}

export interface IncidentListItem {
  id: string
  incident_no: string
  form_kind: 'medical' | 'equipment' | 'near_miss'
  title: string
  status: string
  occurred_at: string | null
  location_path: string | null
  injury_class: 'first_aid' | 'medical_aid' | 'lost_time' | null
  mol_reportable: boolean
}

export interface ActionListItem {
  id: string
  action_no: string
  source_type: string
  source_ref: string | null
  title: string
  status: string
  owner_name: string | null
  due_date: string
  escalation_level: number
  priority: string
}
