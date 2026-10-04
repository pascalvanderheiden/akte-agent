import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { test, expect, request } from "@playwright/test";
import { BACKEND_URL } from "./helpers";

const HOSTED_AGENT_TIMEOUT_MS = 180_000;

function hostedAgentToken(): string | undefined {
  try {
    return execFileSync(
      "az",
      [
        "account",
        "get-access-token",
        "--scope",
        "https://ai.azure.com/.default",
        "--query",
        "accessToken",
        "--output",
        "tsv",
      ],
      { encoding: "utf8", timeout: 15_000 },
    ).trim();
  } catch {
    return undefined;
  }
}

test("hosted agent persists both messages when its Cosmos path is reachable", async () => {
  test.setTimeout(HOSTED_AGENT_TIMEOUT_MS + 30_000);

  const projectEndpoint = process.env.FOUNDRY_PROJECT_ENDPOINT;
  test.skip(!projectEndpoint, "Hosted-agent persistence check skipped: FOUNDRY_PROJECT_ENDPOINT is unavailable");
  const token = hostedAgentToken();
  test.skip(!token, "Hosted-agent persistence check skipped: Azure CLI has no Foundry access token");

  const agentName = process.env.FOUNDRY_AGENT_NAME || "kratos-agent";
  const apiVersion = process.env.FOUNDRY_AGENT_API_VERSION || "v1";
  const endpoint =
    process.env.KRATOS_HOSTED_AGENT_INVOCATIONS_ENDPOINT ||
    `${projectEndpoint!.replace(/\/+$/, "")}/agents/${encodeURIComponent(agentName)}/endpoint/protocols/invocations?api-version=${encodeURIComponent(apiVersion)}`;
  const api = await request.newContext({
    timeout: HOSTED_AGENT_TIMEOUT_MS,
    extraHTTPHeaders: { Authorization: "Bearer " + token },
  });

  let conversationId: string | undefined;
  try {
    const created = await api.post(`${BACKEND_URL}/api/conversations`, {
      data: { useCase: "akte-agent", title: "hosted-agent persistence smoke" },
    });
    expect(created.status(), "create hosted-agent smoke conversation").toBe(201);
    conversationId = (await created.json()).id;
    expect(conversationId, "hosted-agent smoke conversation id").toMatch(/^[0-9a-f-]{36}$/);
    const prompt = `Reply with a short confirmation for hosted persistence smoke ${randomUUID()}.`;
    const response = await api.post(endpoint, {
      data: {
        input: prompt,
        conversationId,
        useCase: "akte-agent",
        persistenceAllowed: true,
      },
      headers: { Accept: "text/event-stream" },
    });
    if (response.status() === 503 && response.headers()["x-kratos-cosmos-path"] === "blocked") {
      test.info().annotations.push({
        type: "blocked",
        description: "Hosted-agent Cosmos private path is unreachable",
      });
      test.skip(true, "Hosted-agent Cosmos private path is unreachable");
    }
    expect(response.status(), "hosted-agent invocation status").toBe(200);
    const body = await response.text();
    const events = [...body.matchAll(/^data:\s*(.+)$/gm)].flatMap(([, value]) => {
      try {
        return [JSON.parse(value)];
      } catch {
        return [];
      }
    });
    expect(events.some((event) => event.event === "done"), "hosted agent completed the invocation").toBe(true);
    expect(events.some((event) => event.event === "error"), "hosted agent returned no error event").toBe(false);

    const diagnostic = events.find(
      (event) => event.event === "kratos_diag" && typeof event.data?.persistence_mode === "string",
    )?.data;
    expect(diagnostic, "hosted agent reports its persistence result").toBeTruthy();
    if (
      diagnostic.persistence_mode === "local" ||
      diagnostic.persistence_mode === "unavailable" ||
      diagnostic.blocked === true
    ) {
      const reason = `Hosted-agent Cosmos path is blocked/unavailable (${diagnostic.persistence_mode})`;
      test.info().annotations.push({ type: "blocked", description: reason });
      test.skip(true, reason);
    }

    expect(diagnostic.persistence_mode, "hosted agent used Cosmos, not local fallback").toBe("cosmos");
    expect(diagnostic.user_message_persisted, "hosted agent persisted the user message").toBe(true);
    expect(diagnostic.assistant_message_persisted, "hosted agent persisted the assistant message").toBe(true);

    const history = await api.get(
      `${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId!)}/messages`,
    );
    expect(history.status(), "history lookup status").toBe(200);
    const messages: { role: string; content: string }[] = await history.json();
    expect(messages, "hosted agent user message is visible in conversation history").toEqual(
      expect.arrayContaining([expect.objectContaining({ role: "user", content: prompt })]),
    );
    expect(
      messages.some((message) => message.role === "assistant" && message.content.trim().length > 0),
      "hosted agent assistant message is visible in conversation history",
    ).toBe(true);
  } finally {
    if (conversationId) {
      await api.delete(`${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}`);
    }
    await api.dispose();
  }
});
