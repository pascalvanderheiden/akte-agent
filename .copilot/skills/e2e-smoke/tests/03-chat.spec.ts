import { execFileSync } from "node:child_process";
import { test, expect, request, type APIRequestContext } from "@playwright/test";
import { BACKEND_URL, chatOnce, CHAT_TIMEOUT_MS } from "./helpers";

// App Insights ingestion is asynchronous, so the denial count is only asserted
// once this run's final log marker is queryable in the same pipeline.
const TELEMETRY_WATERMARK_TIMEOUT_MS = 300_000;
const TELEMETRY_POLL_INTERVAL_MS = 15_000;

function queryCount(appId: string, query: string, what: string): number {
  const output = execFileSync(
    "az",
    ["monitor", "app-insights", "query", "--app", appId, "--analytics-query", query, "--output", "json"],
    { encoding: "utf8", timeout: 30_000 },
  );
  const count = JSON.parse(output).tables?.[0]?.rows?.[0]?.[0];
  expect(typeof count, `${what} query must return a count`).toBe("number");
  return count;
}

async function deleteAndVerifyConversation(api: APIRequestContext, conversationId: string): Promise<void> {
  const url = `${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}`;
  const deleted = await api.delete(url);
  expect(deleted.status(), "delete smoke conversation status").toBe(204);
  const remaining = await api.get(`${url}/messages`);
  expect(remaining.status(), "post-delete history status").toBe(200);
  expect(await remaining.json(), "smoke conversation messages deleted with the conversation").toEqual([]);
}

async function expectNoCosmosFirewallDenials(
  appId: string,
  conversationId: string,
  startedAt: Date,
): Promise<void> {
  // The deletion marker is emitted after run completion and cleanup through
  // the same log exporter as Cosmos denial records.
  const completionMarker = `
    traces
    | where timestamp >= datetime(${startedAt.toISOString()})
    | extend text = strcat(message, " ", tostring(customDimensions))
    | where text has "Conversation deletion completed" and text has "${conversationId}"
    | count
  `;
  const deadline = Date.now() + TELEMETRY_WATERMARK_TIMEOUT_MS;
  while (queryCount(appId, completionMarker, "telemetry completion marker") < 1) {
    if (Date.now() >= deadline) {
      throw new Error(
        `App Insights did not ingest this smoke run's completion marker within ${TELEMETRY_WATERMARK_TIMEOUT_MS / 1000}s; ` +
          "cannot prove the run produced no Cosmos firewall denials",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, TELEMETRY_POLL_INTERVAL_MS));
  }

  const denials = `
    let completedAt = toscalar(
      traces
      | where timestamp >= datetime(${startedAt.toISOString()})
      | extend text = strcat(message, " ", tostring(customDimensions))
      | where text has "Conversation deletion completed" and text has "${conversationId}"
      | summarize max(timestamp)
    );
    union isfuzzy=true
      (traces | project timestamp, text=strcat(message, " ", tostring(customDimensions))),
      (exceptions | project timestamp, text=strcat(outerMessage, " ", innermostMessage, " ", tostring(details)))
    | where timestamp between (datetime(${startedAt.toISOString()}) .. completedAt)
    | where text has "firewall" and text has "Cosmos"
    | count
  `;
  expect(
    queryCount(appId, denials, "Cosmos firewall telemetry"),
    "Cosmos firewall-denial telemetry during smoke run",
  ).toBe(0);
}

test.describe("chat round-trip", () => {
  test.setTimeout(CHAT_TIMEOUT_MS * 2 + 90_000 + TELEMETRY_WATERMARK_TIMEOUT_MS);

  test("agent responds and persists both messages for the Akte use-case", async () => {
    const startedAt = new Date();
    // Best-effort warmup; tolerate cold-start.
    await chatOnce("ping", "akte-agent", 30_000).catch(() => undefined);

    const api = await request.newContext();
    const created = await api.post(`${BACKEND_URL}/api/conversations`, {
      data: { useCase: "akte-agent", title: "e2e-smoke persistence" },
    });
    expect(created.status(), "create smoke conversation status").toBe(201);
    const { id: conversationId } = await created.json();
    // The id is interpolated into KQL below; accept only the backend's UUID shape.
    expect(conversationId, "smoke conversation id").toMatch(/^[0-9a-f-]{36}$/);

    let bodyError: unknown;
    try {
      const prompt = "Reply with exactly one short sentence confirming you are online.";
      const { text, ok, status } = await chatOnce(
        prompt,
        "akte-agent",
        CHAT_TIMEOUT_MS,
        conversationId,
      );

      expect(
        ok,
        `chat should succeed (status=${status}, text snippet="${text.slice(0, 200)}")`,
      ).toBe(true);
      expect(
        text.trim().length,
        "assistant response should be non-empty",
      ).toBeGreaterThan(0);

      const resp = await api.get(
        `${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      );
      expect(resp.status(), "conversation history status").toBe(200);
      const messages: { role: string; content: string }[] = await resp.json();
      expect(messages, "user message persisted").toEqual(
        expect.arrayContaining([expect.objectContaining({ role: "user", content: prompt })]),
      );
      expect(
        messages.some((message) => message.role === "assistant" && message.content.trim().length > 0),
        "assistant message persisted",
      ).toBe(true);
    } catch (error) {
      bodyError = error;
    }

    let cleanupError: unknown;
    try {
      await deleteAndVerifyConversation(api, conversationId);
    } catch (error) {
      cleanupError = error;
    } finally {
      await api.dispose();
    }
    if (bodyError) {
      if (cleanupError) console.error("smoke conversation cleanup also failed:", cleanupError);
      throw bodyError;
    }
    if (cleanupError) throw cleanupError;

    const appId = process.env.KRATOS_APP_INSIGHTS_ID;
    if (!appId) {
      const reason = "Cosmos firewall telemetry check skipped: KRATOS_APP_INSIGHTS_ID is unavailable (Azure telemetry access required)";
      if (process.env.CI) throw new Error(reason);
      test.info().annotations.push({ type: "skip-reason", description: reason });
      console.log(reason);
      return;
    }
    await expectNoCosmosFirewallDenials(appId, conversationId, startedAt);
  });
});
