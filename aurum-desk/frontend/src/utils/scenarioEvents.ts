/**
 * V14.4.1 — scenario.updated event parsing and reduction (pure, unit-tested).
 *
 * The backend publishes through the event bus and the broadcaster wraps every domain event as
 *   { type: 'DOMAIN_EVENT', event: { event_type: 'scenario.updated', aggregate_id, payload: {...}, ... } }
 * A bare `msg.type === 'scenario.updated'` never occurs on the wire.
 */
import type { SMCScenarioView } from '../types/smcScenario';

export const SCENARIO_EVENT_TYPE = 'scenario.updated';

export interface ScenarioEventPayload {
  scenario_id: string;
  instance_id: string;
  snapshot_id: string | null;
  revision: number;
  symbol: string;
  session_id: string | null;
  state: string;
  direction: 'LONG' | 'SHORT' | null;
  digest: string | null;
}

export type ParsedScenarioEvent =
  | { kind: 'UPDATE'; payload: ScenarioEventPayload }
  | { kind: 'IGNORED'; reason: string };

export function parseScenarioUpdated(msg: any, symbol: string): ParsedScenarioEvent {
  if (!msg || msg.type !== 'DOMAIN_EVENT') return { kind: 'IGNORED', reason: 'NOT_DOMAIN_EVENT' };
  const ev = msg.event;
  if (!ev || ev.event_type !== SCENARIO_EVENT_TYPE) return { kind: 'IGNORED', reason: 'OTHER_EVENT' };
  const p = ev.payload;
  if (!p || typeof p !== 'object') return { kind: 'IGNORED', reason: 'PAYLOAD_MISSING' };
  if (typeof p.scenario_id !== 'string' || !p.scenario_id) return { kind: 'IGNORED', reason: 'SCENARIO_ID_MISSING' };
  if (typeof p.instance_id !== 'string' || !p.instance_id) return { kind: 'IGNORED', reason: 'INSTANCE_ID_MISSING' };
  if (!Number.isSafeInteger(p.revision) || p.revision < 1) return { kind: 'IGNORED', reason: 'REVISION_INVALID' };
  if (p.symbol !== symbol) return { kind: 'IGNORED', reason: 'OTHER_SYMBOL' };
  return {
    kind: 'UPDATE',
    payload: {
      scenario_id: p.scenario_id,
      instance_id: p.instance_id,
      snapshot_id: typeof p.snapshot_id === 'string' ? p.snapshot_id : null,
      revision: p.revision,
      symbol: p.symbol,
      session_id: typeof p.session_id === 'string' ? p.session_id : null,
      state: typeof p.state === 'string' ? p.state : 'UNKNOWN',
      direction: p.direction === 'LONG' || p.direction === 'SHORT' ? p.direction : null,
      digest: typeof p.digest === 'string' ? p.digest : null,
    },
  };
}

export type SeenDigests = ReadonlyMap<string, string>;

export function buildScenarioEventKey(p: ScenarioEventPayload): string {
  return `${p.symbol}|${p.scenario_id}|${p.instance_id}|${p.revision}`;
}

export function pruneSeenDigests(
  previous: SeenDigests,
  key: string,
  digest: string | null,
  limit = 256
): SeenDigests {
  if (!digest) return previous;
  const next = new Map(previous);
  next.set(key, digest);
  while (next.size > limit) {
    const oldest = next.keys().next().value;
    if (oldest === undefined) break;
    next.delete(oldest);
  }
  return next;
}

export interface ScenarioReduceResult {
  scenarios: SMCScenarioView[];
  nextSeenDigests: SeenDigests;
  /** true when the event cannot be fully applied from its payload (unknown scenario, revision gap, new instance, or higher revision needing full inventory). */
  resync: boolean;
  /** true when the event was an old revision / duplicate digest and changed nothing. */
  ignored: boolean;
  reason?: string;
}

/**
 * Pure scenario update reducer.
 * Neither `prev` nor `seenDigests` is ever mutated.
 * Old revisions and duplicate (revision, digest) pairs are ignored; a revision gap,
 * an unknown scenario or a changed instance requires a GET resync instead of trusting a partial payload.
 */
export function applyScenarioUpdate(
  prev: readonly SMCScenarioView[],
  upd: ScenarioEventPayload,
  seenDigests: SeenDigests = new Map()
): ScenarioReduceResult {
  const key = buildScenarioEventKey(upd);
  if (upd.digest && seenDigests.get(key) === upd.digest) {
    return { scenarios: [...prev], nextSeenDigests: seenDigests, resync: false, ignored: true, reason: 'DUPLICATE_DIGEST' };
  }

  const idx = prev.findIndex((s) => s.scenario_id === upd.scenario_id);
  if (idx < 0) {
    const nextSeen = pruneSeenDigests(seenDigests, key, upd.digest);
    return { scenarios: [...prev], nextSeenDigests: nextSeen, resync: true, ignored: false, reason: 'UNKNOWN_SCENARIO' };
  }

  const cur = prev[idx];
  if (upd.revision < cur.revision) {
    return { scenarios: [...prev], nextSeenDigests: seenDigests, resync: false, ignored: true, reason: 'OLD_REVISION' };
  }

  if (upd.revision === cur.revision) {
    if (upd.digest && seenDigests.has(key) && seenDigests.get(key) !== upd.digest) {
      const nextSeen = pruneSeenDigests(seenDigests, key, upd.digest);
      return { scenarios: [...prev], nextSeenDigests: nextSeen, resync: true, ignored: false, reason: 'DIGEST_MISMATCH_SAME_REVISION' };
    }
    return { scenarios: [...prev], nextSeenDigests: seenDigests, resync: false, ignored: true, reason: 'SAME_REVISION_NOOP' };
  }

  const nextSeen = pruneSeenDigests(seenDigests, key, upd.digest);
  const gap = upd.revision > cur.revision + 1 || upd.instance_id !== cur.instance_id;
  const next = [...prev];
  next[idx] = {
    ...cur,
    revision: upd.revision,
    state: upd.state,
    direction: upd.direction,
    current_snapshot_id: upd.snapshot_id ?? cur.current_snapshot_id,
  };
  return { scenarios: next, nextSeenDigests: nextSeen, resync: gap || upd.revision > cur.revision, ignored: false };
}
