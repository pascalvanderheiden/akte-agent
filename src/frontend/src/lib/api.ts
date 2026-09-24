import { getApiUrl, getAuthConfig } from "@/lib/config";
import { getMcpAccessToken } from "@/lib/auth";
import { ApplicationError, responseError, type ErrorCode } from "@/lib/errors";
import type {
ModelCatalogue,
  Attachment,
  EvalScenario,
  EvalRun,
  EvalMode,
  TraceList,
  TraceOperation,
  Locale,
} from "@/types";

/**
 * Send a message to the agent and receive streaming SSE events.
 */
export async function streamAgentChat(
  conversationId: string,
  message: string,
  onEvent: (event: { type: string; data: unknown }) => void,
  onError: (error: Error) => void,
  onDone: () => void,
  attachments?: Attachment[],
  useCase?: string,
  locale?: Locale,
  selectedModelId?: string
): Promise<void> {
  try {
    const payload: Record<string, unknown> = { conversationId, message };
    if (attachments && attachments.length > 0) {
      payload.attachments = attachments;
    }
    if (useCase) {
      payload.useCase = useCase;
    }
    if (locale) {
      payload.locale = locale;
    }
    if (selectedModelId) {
      payload.selectedModelId = selectedModelId;
    }

    // When OBO sign-in is configured, attach the user's MCP-scoped access token
    // so the hosted agent can call the OBO MCP server On-Behalf-Of the user.
    // Silent-only (never interactive): an interactive acquisition here opens an
    // MSAL popup, and a popup that is blocked or ignored leaves its promise
    // pending forever, which would hang the send instead of failing. Signing in
    // is the explicit job of the OBO sign-in button; until the user does that,
    // the chat proceeds without a token.
    const authCfg = getAuthConfig();
    if (authCfg) {
      try {
        const token = await getMcpAccessToken(false);
        if (token) {
          payload.mcpAccessTokens = { [authCfg.mcpServerName]: token };
        }
      } catch (err) {
        console.warn("OBO token acquisition failed; continuing without it", err);
      }
    }

    const response = await fetch(`${getApiUrl()}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw await responseError(response, "PROXY_ERROR");
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new ApplicationError("STREAM_ERROR");
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let eventType = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
            continue;
          }
          if (!line.trim()) eventType = "";
          if (line.startsWith("data: ")) {
            const jsonStr = line.slice(6);
            const parsed = JSON.parse(jsonStr);
            const type = parsed.type || eventType;
            if (!type) throw new ApplicationError("STREAM_ERROR");
            onEvent({ type, data: parsed });

            if (type === "done") {
              onDone();
              return;
            }
          }
        }
      }

      throw new ApplicationError("STREAM_ERROR");
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  } catch (err) {
    onError(err instanceof Error ? err : new Error(String(err)));
  }
}

/**
 * Create a new conversation.
 */
export async function createConversation(
  title: string = "New Conversation",
  useCase: string = "generic"
): Promise<{ id: string; title: string; useCase: string }> {
  const response = await fetch(`${getApiUrl()}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, useCase }),
  });

  if (!response.ok) {
    throw await responseError(response, "CREATE_ERROR");
  }

  return response.json();
}

/**
 * Respond to a user input request from the agent.
 */
export async function respondToUserInput(
  conversationId: string,
  requestId: string,
  answer: string
): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/agent/user-input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversationId, requestId, answer }),
  });
  if (!response.ok) {
    throw new Error(`Failed to respond to user input: ${response.status}`);
  }
}

/**
 * Update a conversation's title.
 */
export async function updateConversation(
  conversationId: string,
  updates: { title?: string }
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }
  );
  if (!response.ok) {
    throw await responseError(response, "TITLE_ERROR");
  }
}

/**
 * Delete a conversation.
 */
export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    throw await responseError(response, "DELETE_ERROR");
  }
}

/**
 * List all conversations.
 */
export async function listConversations(): Promise<{ conversations: unknown[] }> {
  const response = await fetch(`${getApiUrl()}/api/conversations`);
  if (!response.ok) {
    throw await responseError(response, "HISTORY_ERROR");
  }
  return response.json();
}

/**
 * Get messages for a conversation.
 */
export async function getConversationMessages(conversationId: string): Promise<unknown[]> {
  const response = await fetch(`${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}/messages`);
  if (!response.ok) {
    throw await responseError(response, "HISTORY_ERROR");
  }
  return response.json();
}

// ─── Use Cases API ───

import type { UseCase } from "@/types";

export async function listUseCases(): Promise<UseCase[]> {
  const response = await fetch(`${getApiUrl()}/api/use-cases`);
  if (!response.ok) throw await responseError(response, "CATALOG_ERROR");
  const data = await response.json();
  if (!Array.isArray(data.useCases)) throw new ApplicationError("CATALOG_ERROR");
  return data.useCases;
}

/**
 * Download a use-case as a self-contained Foundry Hosted Agent ZIP.
 *
 * Returns the raw bytes; the caller is responsible for triggering the
 * browser download (e.g. via createObjectURL + anchor click).
 */
export async function exportUseCase(name: string): Promise<Blob> {
  const response = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(name)}/export`,
    { headers: { Accept: "application/zip" } },
  );
  if (!response.ok) {
    throw await responseError(response, "EXPORT_ERROR");
  }
  return response.blob();
}

/** Result of a persona import (mirrors backend `PersonaImportResponse`). */
export interface PersonaImportResult {
  name: string;
  displayName: string;
  description: string;
  skillCount: number;
  created: boolean;
  files: string[];
}

/**
 * Import a persona from a threadlight-design-compatible manifest.
 *
 * The manifest is built by the host (e.g. agentic-loop-site) and relayed
 * same-origin via sessionStorage; here it is POSTed to the auth-gated Kratos
 * import endpoint. Returns the created persona's slug + metadata.
 */
export async function importPersona(
  manifest: unknown,
  opts: { name?: string; overwrite?: boolean; dedupe?: boolean } = {},
): Promise<PersonaImportResult> {
  const payload: Record<string, unknown> = { manifest };
  if (opts.name) payload.name = opts.name;
  if (opts.overwrite !== undefined) payload.overwrite = opts.overwrite;
  if (opts.dedupe !== undefined) payload.dedupe = opts.dedupe;

  const response = await fetch(`${getApiUrl()}/api/use-cases/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await responseError(response, "IMPORT_ERROR");
  }
  return (await response.json()) as PersonaImportResult;
}

// ─── Skills Admin API ───

import type { Skill, SkillCreate, SkillUpdate } from "@/types";

export async function listSkills(useCase: string = "generic"): Promise<Skill[]> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills?use_case=${encodeURIComponent(useCase)}`);
  if (!response.ok) throw await responseError(response, "SKILLS_ERROR");
  const data = await response.json();
  return data.skills;
}

export async function getSkill(name: string, useCase: string = "generic"): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}?use_case=${encodeURIComponent(useCase)}`);
  if (!response.ok) throw await responseError(response, "SKILLS_ERROR");
  return response.json();
}

export async function createSkill(skill: SkillCreate, useCase: string = "generic"): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills?use_case=${encodeURIComponent(useCase)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
  if (!response.ok) throw await responseError(response, "SKILL_CHANGE_ERROR");
  return response.json();
}

export async function updateSkill(name: string, updates: SkillUpdate, useCase: string = "generic"): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}?use_case=${encodeURIComponent(useCase)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw await responseError(response, "SKILL_CHANGE_ERROR");
  return response.json();
}

export async function deleteSkill(name: string, useCase: string = "generic"): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}?use_case=${encodeURIComponent(useCase)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await responseError(response, "SKILL_CHANGE_ERROR");
}

// ─── Skill Files API ───

import type { SkillFile } from "@/types";

export async function listSkillFiles(skillName: string, useCase: string = "generic"): Promise<{ files: SkillFile[] }> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files?use_case=${encodeURIComponent(useCase)}`
  );
  if (!response.ok) throw await responseError(response, "SKILL_FILE_ERROR");
  return response.json();
}

export async function upsertSkillFile(
  skillName: string,
  filePath: string,
  content: string,
  useCase: string = "generic"
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files/${filePath}?use_case=${encodeURIComponent(useCase)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!response.ok) throw await responseError(response, "SKILL_FILE_ERROR");
}

export async function deleteSkillFile(
  skillName: string,
  filePath: string,
  useCase: string = "generic"
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files/${filePath}?use_case=${encodeURIComponent(useCase)}`,
    { method: "DELETE" }
  );
  if (!response.ok) throw await responseError(response, "SKILL_FILE_ERROR");
}

// ─── System Prompt Admin API ───

import type { SystemPrompt } from "@/types";

export async function getSystemPrompt(useCase?: string): Promise<SystemPrompt> {
  const params = useCase ? `?use_case=${encodeURIComponent(useCase)}` : "";
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt${params}`);
  if (!response.ok) throw await responseError(response, "PROMPT_ERROR");
  return response.json();
}

export async function updateSystemPrompt(content: string, useCase?: string): Promise<SystemPrompt> {
  const params = useCase ? `?use_case=${encodeURIComponent(useCase)}` : "";
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt${params}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw await responseError(response, "PROMPT_ERROR");
  }
  return response.json();
}

export async function resetSystemPrompt(useCase?: string): Promise<void> {
  const params = useCase ? `?use_case=${encodeURIComponent(useCase)}` : "";
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt${params}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw await responseError(response, "PROMPT_ERROR");
  }
}

// ─── MCP Servers Admin API ───

import type { MCPConfig } from "@/types";

export async function getMCPConfig(useCase: string): Promise<MCPConfig> {
  const response = await fetch(`${getApiUrl()}/api/admin/mcp-servers?use_case=${encodeURIComponent(useCase)}`);
  if (!response.ok) throw await responseError(response, "MCP_ERROR");
  return response.json();
}

export async function updateMCPConfig(useCase: string, servers: MCPConfig["servers"]): Promise<MCPConfig> {
  const response = await fetch(`${getApiUrl()}/api/admin/mcp-servers?use_case=${encodeURIComponent(useCase)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ servers }),
  });
  if (!response.ok) throw await responseError(response, "MCP_ERROR");
  return response.json();
}

// ─── Consistency Analysis API ───

import type { AnalysisResult } from "@/types";

export async function analyzeConsistency(
  useCase: string = "generic",
  includeDisabled: boolean = true
): Promise<AnalysisResult> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/analysis/consistency?use_case=${encodeURIComponent(useCase)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ includeDisabled }),
    }
  );
  if (!response.ok) throw await responseError(response, "ANALYSIS_ERROR");
  return response.json();
}

import type { AnalysisIssue, ApplyFixResult } from "@/types";

// ─── APM Admin API ───

import type { ApmStatusResponse, ApmCommandResponse } from "@/types";

export async function getApmStatus(useCase: string): Promise<ApmStatusResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm`
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function installApmPackage(
  useCase: string,
  body: { package: string; ref?: string; dev?: boolean }
): Promise<ApmCommandResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/install`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function uninstallApmPackage(
  useCase: string,
  pkg: string
): Promise<ApmCommandResponse> {
  // Backend route is DELETE /{package:path}, so slashes must be preserved.
  // Encode each segment separately to avoid %2F while still escaping special chars.
  const encodedPkg = pkg.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/${encodedPkg}`,
    { method: "DELETE" }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function installApmMcpServer(
  useCase: string,
  body: {
    name: string;
    transport?: "stdio" | "http" | "sse";
    command?: string;
    args?: string[];
    url?: string;
    env?: Record<string, string>;
  }
): Promise<ApmCommandResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/mcp`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function uninstallApmMcpServer(
  useCase: string,
  name: string
): Promise<ApmCommandResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/mcp/${encodeURIComponent(name)}`,
    { method: "DELETE" }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function syncApm(useCase: string): Promise<ApmCommandResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/sync`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function updateApm(
  useCase: string,
  body: { package?: string } = {}
): Promise<ApmCommandResponse> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/use-cases/${encodeURIComponent(useCase)}/apm/update`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) throw await responseError(response, "APM_ERROR");
  return response.json();
}

export async function applyAnalysisFix(
  issue: AnalysisIssue,
  useCase: string = "generic"
): Promise<ApplyFixResult> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/analysis/apply-fix?use_case=${encodeURIComponent(useCase)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: issue.category,
        title: issue.title,
        description: issue.description,
        recommendation: issue.recommendation,
        affectedSkills: issue.affectedSkills,
      }),
    }
  );
  if (!response.ok) throw await responseError(response, "ANALYSIS_ERROR");
  return response.json();
}

// ─── Evals ─────────────────────────────────────────────────────────────────

async function readJson<T>(response: Response, code: ErrorCode): Promise<T> {
  if (!response.ok) throw await responseError(response, code);
  return response.json();
}

export async function listEvalScenarios(useCase: string): Promise<EvalScenario[]> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/scenarios`,
  );
  const data = await readJson<{ scenarios: EvalScenario[] }>(r, "EVAL_LOAD");
  if (!Array.isArray(data.scenarios)) throw new ApplicationError("EVAL_LOAD");
  return data.scenarios;
}

export async function generateEvalScenarios(
  useCase: string,
  body: { count?: number; persist?: boolean; instructions?: string } = {},
): Promise<{ scenarios: EvalScenario[]; persisted: boolean }> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/scenarios/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 10, persist: false, instructions: "", ...body }),
    },
  );
  const data = await readJson<{ scenarios: EvalScenario[]; persisted: boolean }>(r, "EVAL_GENERATE");
  if (!Array.isArray(data.scenarios)) throw new ApplicationError("EVAL_GENERATE");
  return data;
}

export async function upsertEvalScenario(
  useCase: string,
  scenario: EvalScenario,
): Promise<EvalScenario> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/scenarios/${encodeURIComponent(scenario.name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scenario),
    },
  );
  return readJson(r, "EVAL_SAVE");
}

export async function deleteEvalScenario(useCase: string, name: string): Promise<void> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/scenarios/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!r.ok && r.status !== 404) {
    throw await responseError(r, "EVAL_DELETE");
  }
}

export async function startEvalRun(
  useCase: string,
  body: { mode: EvalMode; scenarios?: string[] },
): Promise<EvalRun> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return readJson(r, "EVAL_START");
}

export async function listEvalRuns(useCase: string): Promise<EvalRun[]> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/runs`,
  );
  const data = await readJson<{ runs: EvalRun[] }>(r, "EVAL_LOAD");
  if (!Array.isArray(data.runs)) throw new ApplicationError("EVAL_LOAD");
  return data.runs;
}

export async function getEvalRun(useCase: string, runId: string): Promise<EvalRun> {
  const r = await fetch(
    `${getApiUrl()}/api/use-cases/${encodeURIComponent(useCase)}/evals/runs/${encodeURIComponent(runId)}`,
  );
  return readJson(r, "EVAL_POLL");
}

// ─── Traces ────────────────────────────────────────────────────────────────

export async function listTraceOperations(filters: {
  useCase?: string;
  conversationId?: string;
  evalRunId?: string;
  lookbackHours?: number;
  maxOperations?: number;
}): Promise<TraceList> {
  const params = new URLSearchParams();
  if (filters.useCase) params.set("use_case", filters.useCase);
  if (filters.conversationId) params.set("conversation_id", filters.conversationId);
  if (filters.evalRunId) params.set("eval_run_id", filters.evalRunId);
  if (filters.lookbackHours) params.set("hours", String(filters.lookbackHours));
  if (filters.maxOperations) params.set("limit", String(filters.maxOperations));
  const qs = params.toString();
  const r = await fetch(`${getApiUrl()}/api/traces/operations${qs ? `?${qs}` : ""}`);
  const data = await readJson<TraceList>(r, "TRACE_LOAD");
  if (!Array.isArray(data.operations) || !data.summary) throw new ApplicationError("TRACE_LOAD");
  return data;
}

export async function getTraceOperation(
  operationId: string,
  lookbackHours = 168,
): Promise<TraceOperation> {
  const r = await fetch(
    `${getApiUrl()}/api/traces/operations/${encodeURIComponent(operationId)}?hours=${lookbackHours}`,
  );
  const data = await readJson<TraceOperation>(r, "TRACE_DETAIL");
  if (!Array.isArray(data.spans) || !Array.isArray(data.logs)) throw new ApplicationError("TRACE_DETAIL");
  return data;
}

// ─── Models API ───

/**
 * Fetch the available model catalogue.
 */
export async function listModels(): Promise<ModelCatalogue> {
  const response = await fetch(`${getApiUrl()}/api/models`);
  if (!response.ok) {
    throw await responseError(response, "MODELS_ERROR");
  }
  const data = await response.json();
  return data as ModelCatalogue;
}

/**
 * Update conversation's selected model.
 */
export async function updateConversationModel(
  conversationId: string,
  modelId: string
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selectedModelId: modelId }),
    }
  );
  if (!response.ok) {
    throw await responseError(response, "MODEL_ERROR");
  }
}
