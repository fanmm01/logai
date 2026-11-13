export type Effect = Record<string, any>;
export type Character = { serial: string; name: string; color: string; emblem: string; role: string; portrait: string; avatar: string; attrs: Record<string, number>; spells: Effect[] };
export type Skill = { index: number; name: string; command: string; timing: string; mp_cost: number | string; san_cost?: number | string; formula?: string; effects: Effect[]; available: boolean; reason: string; cooldown: number; revive_candidates: string[]; once: boolean };
export type Unit = { uid: string; serial: string; name: string; team: string; coord: string; hp: number; hp_max: number; mp: number; mp_max: number; san: number; summon: boolean; owner?: string; representative: string; group_count: number; spirit?: number; statuses: Effect[]; buffs: Effect[]; inventory: Effect[]; controller: string; move_remaining: number; actions: Record<string, number>; phase: number; auto_reaction?: { enabled: boolean; choice: string; remaining: number; revision: number }; reaction_choices?: string[] };
export type Snapshot = { state: { phase: string; round: number; activeIndex: number }; map: { width: number; height: number; obstacles: Record<string, any>; occupants: Record<string, string> }; initiative: Effect[]; units: Unit[]; turn: { uid: string; slot_uid: string; skills: Skill[]; controller: string; actions: Record<string, number> } | null; effects: Effect[]; graveyard: Effect[]; reaction: { defender: string; attacker: string; defender_name: string; skill: string; damage: string; roll: number; controller: string; choices: string[] } | null; result: { winner: string } | null; revision: number };
export type BattleEvent = { id: number; kind: string; output: string; time: number; snapshot: Snapshot };
export type Member = { id: string; name: string; ready: boolean; spectator: boolean };
export type Room = { code: string; name: string; local: boolean; me: string; host: string; members: Member[]; revision: number; running: boolean; speed: number; controls: Record<string, string>; snapshot: Snapshot; history: BattleEvent[]; invite_urls: string[]; tournament?: { id: string; series: string; next_available: boolean } | null };
export type Simulation = { id: string; status: string; completed: number; params: Effect; message?: string; result: { wins_a: number; wins_b: number; rate_a: number; rate_b: number; average_rounds: number; elapsed: number; timeouts: number } | null };

export async function api<T>(path: string, data?: unknown): Promise<T> {
  const response = await fetch('/api/ui' + path, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await response.json().catch(() => ({ message: '服务返回了无法识别的响应' }));
  if (!response.ok) throw new Error(result.message || '请求失败');
  return result;
}
