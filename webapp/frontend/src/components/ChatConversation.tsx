import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import type { ChatResult, TokenUsage } from "../lib/api";
import { formatAgentReply } from "../lib/jsonToMarkdown";

export type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  agent?: string;
  at?: string;
  usage?: TokenUsage | null;
  raw?: ChatResult | null;
  error?: boolean;
  fromJson?: boolean;
};

/** Format agent reply text; JSON → markdown via json2md. */
export function extractChatText(raw: string | null | undefined): string {
  return formatAgentReply(raw).markdown;
}

function formatTokens(usage?: TokenUsage | null): string | null {
  if (!usage) return null;
  const total = usage.totalTokens;
  const prompt = usage.promptTokens;
  const completion = usage.completionTokens;
  if (total != null) return `${total} tokens`;
  if (prompt != null || completion != null) {
    return `${prompt ?? "—"} → ${completion ?? "—"} tokens`;
  }
  return null;
}

function AssistantBubble({
  turn,
  onInspect,
}: {
  turn: ChatTurn;
  onInspect?: (raw: ChatResult) => void;
}) {
  const [showExtras, setShowExtras] = useState(false);
  const tokens = formatTokens(turn.usage);

  const formatted = useMemo(() => {
    if (turn.error) return { markdown: turn.text, fromJson: false as const };
    const source = turn.raw?.output_markdown || turn.raw?.reply || turn.text || "";
    return formatAgentReply(source);
  }, [turn.error, turn.raw, turn.text]);

  // Prefer formatted agent content; never leave raw chat.completion dump inline
  const displayMd = formatted.markdown || turn.text;

  return (
    <div className={`flex flex-col gap-1.5 w-full max-w-[92%] ${turn.error ? "opacity-90" : ""}`}>
      <div className="flex items-center gap-2 w-full text-[11px] text-[var(--muted)]">
        <span className="font-semibold text-[var(--sea)]">
          {(turn.agent || "agent").replace("-agent", "")}
        </span>
        {turn.at && <span>{new Date(turn.at).toLocaleTimeString()}</span>}
        {tokens && (
          <span className="ml-auto text-right font-mono text-[var(--sea)] tabular-nums shrink-0">
            {tokens}
          </span>
        )}
      </div>

      {/* Actual agent response — inline */}
      <div
        className={`rounded-2xl rounded-tl-md px-3.5 py-2.5 text-[15px] leading-[1.65] ${
          turn.error
            ? "bg-ember/10 border border-ember/25 text-ember whitespace-pre-wrap"
            : "bg-[var(--mist)] border border-[var(--line)] text-[var(--ink)]"
        }`}
      >
        {turn.error ? (
          displayMd || <span className="italic text-[var(--muted)]">No reply text returned.</span>
        ) : displayMd ? (
          <div className="chat-md">
            <ReactMarkdown>{displayMd}</ReactMarkdown>
          </div>
        ) : (
          <span className="italic text-[var(--muted)]">No reply text returned.</span>
        )}
      </div>

      {turn.raw && (
        <div className="flex flex-wrap gap-2 items-center">
          <button
            type="button"
            className="text-[11px] text-[var(--sea)] underline-offset-2 hover:underline"
            onClick={() => setShowExtras((v) => !v)}
          >
            {showExtras ? "Hide details" : "Show details"}
          </button>
          {onInspect && (
            <button
              type="button"
              className="text-[11px] text-[var(--muted)] underline-offset-2 hover:underline"
              onClick={() => onInspect(turn.raw!)}
            >
              Tools / guardrails
            </button>
          )}
        </div>
      )}

      {showExtras && turn.raw && (
        <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-solid)] p-2.5 space-y-2">
          <div className="text-[10px] uppercase tracking-[0.12em] font-semibold text-[var(--muted)]">
            Response details
          </div>
          <pre className="text-[10px] whitespace-pre-wrap max-h-56 overflow-auto font-mono text-[var(--ink)]/75">
            {JSON.stringify(
              {
                responseId: turn.raw.responseId,
                previousResponseId: turn.raw.previousResponseId,
                sessionId: turn.raw.sessionId,
                usage: turn.raw.usage,
                model: turn.raw.model,
                tools: turn.raw.tools,
                toolExecutions: turn.raw.toolExecutions,
                skills: turn.raw.skills,
                mcps: turn.raw.mcps,
                trace: turn.raw.trace,
                guardrails: turn.raw.guardrails,
              },
              null,
              2
            )}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ChatConversation({
  turns,
  agent,
  agents,
  lockedAgents,
  input,
  busy,
  onInputChange,
  onAgentChange,
  onSend,
  onStop,
  onInspect,
  onNewChat,
}: {
  turns: ChatTurn[];
  agent: string;
  agents: string[];
  lockedAgents?: Record<string, boolean>;
  input: string;
  busy: boolean;
  onInputChange: (v: string) => void;
  onAgentChange: (id: string) => void;
  onSend: () => void;
  onStop?: () => void;
  onInspect?: (raw: ChatResult) => void;
  onNewChat?: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  useEffect(() => {
    if (!busy) inputRef.current?.focus();
  }, [busy, turns.length]);

  function keyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy && input.trim()) onSend();
    }
  }

  return (
    <section className="panel flex-1 min-w-0 min-h-[320px] flex flex-col overflow-hidden fade-in">
      <div className="shrink-0 px-3 pt-3 pb-2 border-b border-[var(--line)] flex flex-wrap gap-2 items-center justify-between">
        <div className="flex flex-wrap gap-1">
          {agents.map((a) => {
            const locked = lockedAgents?.[a] === false;
            return (
              <button
                key={a}
                type="button"
                onClick={() => onAgentChange(a)}
                className={`text-xs px-2 py-1 rounded-lg border transition ${
                  agent === a
                    ? "bg-ink text-sand border-ink"
                    : "bg-[var(--mist)] border-[var(--line)] hover:border-sea/40"
                }`}
              >
                {a.replace("-agent", "")}
                {locked && <span className="ml-1 text-ember">locked</span>}
              </button>
            );
          })}
        </div>
        {onNewChat && (
          <button
            type="button"
            className="text-[11px] font-semibold text-[var(--sea)] hover:underline shrink-0"
            onClick={onNewChat}
            disabled={busy}
          >
            New chat
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {!turns.length && !busy && (
          <div className="h-full min-h-[160px] grid place-items-center text-center">
            <div>
              <p className="font-display text-xl text-[var(--muted)]">Conversation</p>
              <p className="text-sm text-[var(--muted)] mt-1">
                Ask something below — replies stay in this thread so you can continue.
              </p>
            </div>
          </div>
        )}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} className="flex justify-end">
              <div className="max-w-[85%]">
                <div className="text-[11px] text-[var(--muted)] text-right mb-1 font-semibold">You</div>
                <div className="rounded-2xl rounded-tr-md px-3.5 py-2.5 text-[15px] leading-[1.65] whitespace-pre-wrap bg-sea text-sand">
                  {turn.text}
                </div>
              </div>
            </div>
          ) : (
            <div key={turn.id} className="flex justify-start">
              <AssistantBubble turn={turn} onInspect={onInspect} />
            </div>
          )
        )}

        {busy && (
          <div className="flex justify-start">
            <div className="rounded-2xl rounded-tl-md px-3.5 py-2.5 text-sm text-[var(--sea)] bg-[var(--mist)] border border-[var(--line)] flex items-center gap-2">
              <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-sea border-t-transparent animate-spin" />
              Thinking…
              {onStop && (
                <button
                  type="button"
                  className="ml-2 text-[11px] font-semibold text-ember underline-offset-2 hover:underline"
                  onClick={onStop}
                >
                  Stop
                </button>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-[var(--line)] p-3 bg-[var(--panel-solid)]">
        <textarea
          ref={inputRef}
          className="w-full min-h-[72px] max-h-40 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3 text-sm outline-none focus:ring-2 focus:ring-sea/25 resize-y placeholder:text-[var(--muted)]"
          placeholder="Continue the conversation…"
          value={input}
          disabled={busy}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={keyDown}
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[11px] text-[var(--muted)]">Enter to send · Shift+Enter for new line</span>
          <div className="flex items-center gap-2">
            {busy && onStop ? (
              <button type="button" className="btn-ghost text-ember border-ember/25" onClick={onStop}>
                Stop
              </button>
            ) : (
              <button
                type="button"
                className="btn-accent"
                disabled={busy || !input.trim()}
                onClick={onSend}
              >
                Send
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
