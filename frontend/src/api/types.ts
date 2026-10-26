export type Role = "admin" | "member";
export type AreaRole = "owner" | "editor" | "viewer";
export type PresenceState = "online" | "working" | "meeting" | "paused" | "offline";

export interface Avatar {
  skin?: string;
  hair?: string;
  shirt?: string;
  pants?: string;
  style?: number;
}

export interface UserPublic {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  avatar: Avatar;
  is_active: boolean;
}

export interface UserPrivate extends UserPublic {
  email: string | null;
  preferences: Record<string, unknown>;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Area {
  id: string;
  slug: string;
  name: string;
  description: string;
  room_key: string;
  color: string;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  version: number;
  my_role: AreaRole | null;
  member_count?: number;
  template_key: string | null;
  icon: string;
  enabled: boolean;
}

export interface OrgPublic {
  platform_name: string;
  legal_name: string;
  short_mark: string;
  primary_color: string;
  secondary_color: string;
  logo_url: string | null;
  timezone: string;
}

export interface TimePolicy {
  auto_close: "none" | "last_activity";
  max_open_hours: number;
  disconnect_minutes: number;
  visibility: "all" | "admins";
}

export interface AreaTemplate {
  key: string;
  name: string;
  description: string;
  icon: string;
  room_key: string;
  module: string;
  member_role: AreaRole;
  required: boolean;
  area_id: string | null;
  area_name: string | null;
  enabled: boolean;
}

export interface OrgFull extends OrgPublic {
  id: string;
  description: string;
  time: TimePolicy;
  setup_completed: boolean;
  setup_completed_at: string | null;
  version: number;
  templates?: AreaTemplate[];
}

export interface Me {
  org: OrgPublic;
  setup_required: boolean;
  user: UserPrivate;
  csrf_token: string;
  session: { id: string; expires_at: string };
  active_area: Area | null;
  status: { declared: string; message: string; task_id: string | null };
}

export interface Person extends UserPublic {
  online: boolean;
  state: PresenceState;
  declared: string;
  message: string;
  room_key: string | null;
  area_id: string | null;
  area_entered_at: string | null;
  task: { id: string; title: string; status: string; project_id: string; project_name: string } | null;
  last_seen_at: string | null;
  last_activity: { summary: string; ts: string; kind: string } | null;
  projects: { id: string; name: string; status: string }[];
  work_summary: { todo: number; in_progress: number; blocked: number; done_last_7_days: number };
  status_updated_at: string | null;
  is_me: boolean;
  unread_notifications: number;
  clock: { status: "open" | "pending_close"; clock_in_at: string; running_seconds: number } | null;
  claude: ClaudeAgent;
}

/** IA de un socio (su Claude conectado por MCP o su API key). */
export interface ClaudeAgent {
  name: string;
  connected: boolean;
  subscription: boolean;
  api: boolean;
  assignable: boolean;
  client: string | null;
  last_used_at: string | null;
  pending: number;
  state: "working" | "waiting" | "idle" | "offline";
  working_task_id: string | null;
  working_on?: { title: string; project_id: string; project_name: string } | null;
}

export interface AgentInfo {
  key: string;
  name: string;
  status: string;
  current: string;
  last_result: string;
  stats: Record<string, unknown>;
  heartbeat_at: string | null;
  updated_at: string | null;
}

export interface Room {
  key: string;
  name: string;
  module: string;
  enabled: boolean;
  template_area_id: string | null;
  areas: Area[];
}

export interface IntegrationStatus {
  key: string;
  label: string;
  status: "ok" | "warn" | "error" | "off";
  detail: string;
}

export interface OfficeState {
  org: OrgPublic;
  integrations: IntegrationStatus[];
  rooms: Room[];
  people: Person[];
  agents: AgentInfo[];
  claude_connections: { user_id: string; status: string; verified_at: string | null }[];
  server_time: string;
}

export interface ActivityItem {
  id: number;
  ts: string;
  user_id: string | null;
  actor_kind: string;
  actor_name: string;
  area_id: string | null;
  project_id: string | null;
  kind: string;
  summary: string;
  ref_type: string | null;
  ref_id: string | null;
}

export type EntryStatus = "open" | "pending_close" | "closed";
export type CloseKind = "manual" | "auto" | "declared" | "admin";

export interface TimeEntry {
  id: string;
  user_id: string;
  user_name: string;
  local_date: string;
  clock_in_at: string;
  clock_out_at: string | null;
  status: EntryStatus;
  close_kind: CloseKind | null;
  duration_seconds: number | null;
  running_seconds: number | null;
  pending_since: string | null;
  pending_reason: "max_duration" | "disconnected" | null;
  approval_status: "pending" | "approved" | "rejected";
  approved_by: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  approval_note: string;
  in_note: string;
  out_note: string;
  close_reason: string;
  closed_by_name: string | null;
  in_source: string;
  out_source: string | null;
}

export interface TimeTotals {
  registered_seconds: number;
  approved_seconds: number;
  in_progress_seconds: number;
  pending_approval_seconds: number;
  rejected_seconds: number;
  entries: number;
}

export interface TimeStatus {
  server_time: string;
  timezone: string;
  open: TimeEntry | null;
  last_closed: TimeEntry | null;
  today: TimeTotals;
  policy: TimePolicy;
  can_approve: boolean;
}

export interface TimeSummary {
  from: string;
  to: string;
  timezone: string;
  days: ({ date: string } & TimeTotals)[];
  weeks: ({ week: string } & TimeTotals)[];
  months: ({ month: string } & TimeTotals)[];
  totals: TimeTotals;
  people: ({ user_id: string; user_name: string } & TimeTotals)[];
}

export interface TimeEvent {
  id: number;
  entry_id: string | null;
  user_id: string;
  user_name: string;
  actor_name: string;
  kind: string;
  at: string;
  source: string;
  detail: Record<string, unknown>;
  note: string;
  audit_id: number | null;
}

export interface MeetingPeer {
  peer_id: string;
  user_id: string;
  display_name: string;
  muted: boolean;
  joined_at: string;
}
