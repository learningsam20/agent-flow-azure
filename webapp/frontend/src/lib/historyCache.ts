import type { ChatSession, FlowRun } from "./api";

const RUNS_KEY = (oid: string) => `afa_runs_v1_${oid}`;
const CHATS_KEY = (oid: string) => `afa_chats_v1_${oid}`;

export function loadCachedHistory(oid: string): { runs: FlowRun[]; chats: ChatSession[] } {
  if (!oid || typeof localStorage === "undefined") return { runs: [], chats: [] };
  try {
    const runs = JSON.parse(localStorage.getItem(RUNS_KEY(oid)) || "[]") as FlowRun[];
    const chats = JSON.parse(localStorage.getItem(CHATS_KEY(oid)) || "[]") as ChatSession[];
    return {
      runs: Array.isArray(runs) ? runs : [],
      chats: Array.isArray(chats) ? chats : [],
    };
  } catch {
    return { runs: [], chats: [] };
  }
}

export function saveCachedHistory(oid: string, runs: FlowRun[], chats: ChatSession[]): void {
  if (!oid || typeof localStorage === "undefined") return;
  try {
    // Cap payload — keep recent full runs so click-to-open is instant after refresh.
    localStorage.setItem(RUNS_KEY(oid), JSON.stringify((runs || []).slice(0, 40)));
    localStorage.setItem(CHATS_KEY(oid), JSON.stringify((chats || []).slice(0, 40)));
  } catch {
    /* quota / private mode */
  }
}
