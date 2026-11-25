import json2md from "json2md";

type MdInput = Parameters<typeof json2md>[0];

const MD_KEYS = new Set([
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "p",
  "blockquote",
  "img",
  "ul",
  "ol",
  "hr",
  "code",
  "table",
  "link",
]);

function isJson2mdShape(value: unknown): boolean {
  if (!Array.isArray(value)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const keys = Object.keys(value as object);
      return keys.length === 1 && MD_KEYS.has(keys[0]);
    }
    return false;
  }
  return (
    value.length > 0 &&
    value.every(
      (item) =>
        item &&
        typeof item === "object" &&
        !Array.isArray(item) &&
        Object.keys(item as object).some((k) => MD_KEYS.has(k))
    )
  );
}

function titleCaseKey(key: string): string {
  return key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function primitive(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** Parse JSON, or a Python/repr-ish dict string with single quotes. */
export function tryParseStructured(raw: string): unknown | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  const fence = trimmed.match(/^```(?:json)?\s*([\s\S]*?)```$/i);
  const candidate = fence ? fence[1].trim() : trimmed;
  if (!(candidate.startsWith("{") || candidate.startsWith("[") || candidate.startsWith("'{"))) {
    return undefined;
  }
  try {
    return JSON.parse(candidate);
  } catch {
    /* try python-ish */
  }
  try {
    // Best-effort: single-quoted dict/list → JSON
    const normalized = candidate
      .replace(/\bNone\b/g, "null")
      .replace(/\bTrue\b/g, "true")
      .replace(/\bFalse\b/g, "false")
      .replace(/'/g, '"');
    return JSON.parse(normalized);
  } catch {
    return undefined;
  }
}

/** Pull the human-visible reply out of OpenAI/Foundry chat.completion payloads. */
export function extractAssistantContent(parsed: unknown): string | null {
  if (!parsed || typeof parsed !== "object") return null;
  const o = parsed as Record<string, unknown>;

  // Already normalized agent payload
  for (const key of ["output_markdown", "reply", "content", "text", "summary"]) {
    const v = o[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }

  // chat.completion shape
  const choices = o.choices;
  if (Array.isArray(choices) && choices[0] && typeof choices[0] === "object") {
    const choice = choices[0] as Record<string, unknown>;
    const msg = (choice.message || choice.delta) as Record<string, unknown> | undefined;
    if (msg) {
      const c = msg.content;
      if (typeof c === "string" && c.trim()) return c.trim();
      if (Array.isArray(c)) {
        const parts = c
          .map((p) => {
            if (typeof p === "string") return p;
            if (p && typeof p === "object" && typeof (p as { text?: string }).text === "string") {
              return (p as { text: string }).text;
            }
            return "";
          })
          .join("");
        if (parts.trim()) return parts.trim();
      }
      if (typeof msg.refusal === "string" && msg.refusal.trim()) return msg.refusal.trim();
    }
    if (typeof choice.text === "string" && choice.text.trim()) return choice.text.trim();
  }

  // Nested message object
  if (o.message && typeof o.message === "object") {
    const m = o.message as Record<string, unknown>;
    if (typeof m.content === "string" && m.content.trim()) return m.content.trim();
  }

  return null;
}

function chatCompletionToMd(parsed: Record<string, unknown>): MdInput[] {
  const content = extractAssistantContent(parsed);
  const choice = ((parsed.choices as unknown[]) || [])[0] as Record<string, unknown> | undefined;
  const finish = choice?.finish_reason ? String(choice.finish_reason) : null;
  const usage = (parsed.usage || {}) as Record<string, unknown>;
  const details = (usage.completion_tokens_details || {}) as Record<string, unknown>;
  const out: MdInput[] = [];

  if (content) {
    out.push({ p: content });
  } else {
    out.push({
      p: finish === "length"
        ? "No visible reply — the model hit the token limit while reasoning. Increase completion budget and retry."
        : "No visible assistant content in this response.",
    });
  }

  const meta: string[] = [];
  if (parsed.model) meta.push(`Model: ${parsed.model}`);
  if (finish) meta.push(`Finish: ${finish}`);
  if (usage.total_tokens != null) meta.push(`Total tokens: ${usage.total_tokens}`);
  if (details.reasoning_tokens != null) meta.push(`Reasoning tokens: ${details.reasoning_tokens}`);
  if (meta.length) {
    out.push({ h4: "Response meta" });
    out.push({ ul: meta });
  }
  return out;
}

function isChatCompletion(value: unknown): value is Record<string, unknown> {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Array.isArray((value as Record<string, unknown>).choices) &&
    typeof (value as Record<string, unknown>).object === "string" &&
    String((value as Record<string, unknown>).object).includes("chat.completion")
  );
}

/** Convert arbitrary JSON into json2md element descriptors. */
export function valueToJson2md(value: unknown, depth = 0): MdInput[] {
  if (value == null) return [{ p: "—" }];
  if (typeof value === "string") return [{ p: value }];
  if (typeof value === "number" || typeof value === "boolean") return [{ p: String(value) }];

  if (isChatCompletion(value)) {
    return chatCompletionToMd(value);
  }

  if (Array.isArray(value)) {
    if (!value.length) return [{ p: "_Empty list_" }];
    if (value.every((v) => v == null || typeof v === "string" || typeof v === "number" || typeof v === "boolean")) {
      return [{ ul: value.map((v) => primitive(v)) }];
    }
    if (value.every((v) => v && typeof v === "object" && !Array.isArray(v)) && value.length > 0) {
      const rows = value as Record<string, unknown>[];
      const headers = Array.from(
        rows.reduce((set, row) => {
          Object.keys(row).forEach((k) => set.add(k));
          return set;
        }, new Set<string>())
      );
      const allPrimitive = rows.every((row) =>
        headers.every((h) => {
          const v = row[h];
          return v == null || typeof v === "string" || typeof v === "number" || typeof v === "boolean";
        })
      );
      if (allPrimitive && headers.length > 0 && headers.length <= 8) {
        return [
          {
            table: {
              headers: headers.map(titleCaseKey),
              rows: rows.map((row) => headers.map((h) => primitive(row[h]))),
            },
          },
        ];
      }
    }
    return value.flatMap((item, i) => [
      { [depth < 1 ? "h3" : "h4"]: `Item ${i + 1}` } as MdInput,
      ...valueToJson2md(item, depth + 1),
    ]);
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return [{ p: "_Empty object_" }];

    const proseKeys = ["output_markdown", "reply", "content", "message", "summary", "recommendation", "text"];
    const prose = proseKeys.find((k) => typeof (value as Record<string, unknown>)[k] === "string");
    if (prose) {
      const text = String((value as Record<string, unknown>)[prose]).trim();
      if (text) {
        const rest = entries.filter(([k]) => k !== prose);
        const blocks: MdInput[] = [{ p: text }];
        if (rest.length && depth === 0) {
          // Keep primary reply first; optional structured extras as subsections
          for (const [key, val] of rest) {
            if (val == null) continue;
            if (typeof val === "string" || typeof val === "number" || typeof val === "boolean") {
              blocks.push({ p: `**${titleCaseKey(key)}:** ${primitive(val)}` });
            } else {
              blocks.push({ h4: titleCaseKey(key) });
              blocks.push(...valueToJson2md(val, depth + 1));
            }
          }
        }
        return blocks;
      }
    }

    return entries.flatMap(([key, val]) => {
      const heading = titleCaseKey(key);
      if (val == null || typeof val === "string" || typeof val === "number" || typeof val === "boolean") {
        return [{ p: `**${heading}:** ${primitive(val)}` }];
      }
      if (
        Array.isArray(val) &&
        val.every((v) => v == null || typeof v === "string" || typeof v === "number" || typeof v === "boolean")
      ) {
        return [{ p: `**${heading}:**` }, { ul: val.map((v) => primitive(v)) }];
      }
      return [{ [depth < 1 ? "h3" : "h4"]: heading } as MdInput, ...valueToJson2md(val, depth + 1)];
    });
  }

  return [{ p: String(value) }];
}

export type FormattedReply = {
  /** Markdown shown inline as the agent response */
  markdown: string;
  /** True when source was structured JSON converted via json2md */
  fromJson: boolean;
  /** Parsed object when fromJson */
  parsed?: unknown;
  /** True when payload was a chat.completion shell with empty content */
  emptyModelContent?: boolean;
};

/**
 * Format an agent reply for display. JSON / chat.completion payloads use json2md.
 * Prefer the actual assistant content when present.
 */
export function formatAgentReply(raw: string | null | undefined): FormattedReply {
  if (!raw) return { markdown: "", fromJson: false };
  const trimmed = raw.trim();
  if (!trimmed) return { markdown: "", fromJson: false };

  const parsed = tryParseStructured(trimmed);
  if (parsed === undefined) {
    return { markdown: trimmed, fromJson: false };
  }

  const assistant = extractAssistantContent(parsed);
  const emptyModelContent =
    isChatCompletion(parsed) && !assistant;

  try {
    // If we have clear assistant prose, show that as primary markdown
    // (still mark fromJson so callers can offer raw view).
    if (assistant && !assistant.trim().startsWith("{")) {
      // Nested JSON inside content → format that instead
      const nested = tryParseStructured(assistant);
      if (nested !== undefined) {
        const md = json2md(isJson2mdShape(nested) ? (nested as MdInput) : valueToJson2md(nested)).trim();
        return { markdown: md || assistant, fromJson: true, parsed: nested };
      }
      return { markdown: assistant, fromJson: true, parsed };
    }

    const input: MdInput = isJson2mdShape(parsed) ? (parsed as MdInput) : valueToJson2md(parsed);
    const markdown = json2md(input).trim();
    return {
      markdown: markdown || trimmed,
      fromJson: true,
      parsed,
      emptyModelContent,
    };
  } catch {
    try {
      const markdown = json2md(valueToJson2md(parsed)).trim();
      return { markdown: markdown || trimmed, fromJson: true, parsed, emptyModelContent };
    } catch {
      return {
        markdown: assistant || trimmed,
        fromJson: false,
        parsed,
        emptyModelContent,
      };
    }
  }
}
