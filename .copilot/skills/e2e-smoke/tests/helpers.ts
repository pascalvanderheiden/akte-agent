/**
 * Shared helpers + env-derived URLs used by every spec.
 *
 * No deployment endpoints are hardcoded here — this is a public repo. URLs are
 * resolved by `run.sh` from the local `azd` environment (`.azure/`, gitignored)
 * and exported as KRATOS_FRONTEND_URL / KRATOS_BACKEND_URL, or supplied
 * manually. Missing values fail fast rather than silently targeting a stale env.
 */
function requiredUrl(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(
      `${name} is not set. Run this suite via ./run.sh (which resolves endpoints ` +
        `from 'azd env get-values'), or export ${name} explicitly.`,
    );
  }
  return value.replace(/\/+$/, "");
}

export const FRONTEND_URL = requiredUrl("KRATOS_FRONTEND_URL");

export const BACKEND_URL = requiredUrl("KRATOS_BACKEND_URL");

export const USE_CASES = (process.env.KRATOS_USE_CASES || "generic")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export const CHAT_TIMEOUT_MS = Number(process.env.CHAT_TIMEOUT_MS || 60_000);
export const TRACES_LOOKBACK_HOURS = Number(process.env.TRACES_LOOKBACK_HOURS || 6);

/**
 * Stream-aware POST to /api/agent/chat that aggregates the SSE response into
 * a single concatenated text. The backend yields lines like:
 *   data: {"event":"content","data":{"content":"..."}}
 *   data: {"event":"done"}
 * We collect every content chunk and return the concatenated string.
 */
export async function chatOnce(
  prompt: string,
  useCase: string,
  timeoutMs = CHAT_TIMEOUT_MS,
): Promise<{ text: string; ok: boolean; status: number }> {
  const conversationId = `e2e-smoke-${Date.now()}`;
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), timeoutMs);

  try {
    const resp = await fetch(`${BACKEND_URL}/api/agent/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        message: prompt,
        useCase: useCase,
        conversationId: conversationId,
      }),
      signal: ac.signal,
    });

    if (!resp.ok || !resp.body) {
      const body = resp.body ? await resp.text() : "";
      return { text: body, ok: false, status: resp.status };
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let collected = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try {
          const evt = JSON.parse(payload);
          const ev = evt.event ?? evt.type;
          if (ev === "content") {
            collected += evt.data?.content ?? evt.content ?? "";
          } else if (ev === "done") {
            return { text: collected, ok: true, status: resp.status };
          } else if (ev === "error") {
            return {
              text: collected + `\n[error: ${evt.data?.message ?? evt.message ?? "unknown"}]`,
              ok: false,
              status: resp.status,
            };
          }
        } catch {
          /* ignore non-JSON keepalive lines */
        }
      }
    }
    return { text: collected, ok: collected.length > 0, status: resp.status };
  } finally {
    clearTimeout(to);
  }
}
