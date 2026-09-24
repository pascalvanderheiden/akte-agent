/**
 * #21 browser acceptance: synthetic HTTP fixtures, NOT live model evaluations.
 * Exercises the existing UI/API boundary at root or KRATOS_FRONTEND_URL's mount.
 * All external requests are blocked; no cloud access, model calls or API writes.
 */
import { test, expect, type Page } from "@playwright/test";
import { createRequire } from "node:module";
import { FRONTEND_URL } from "./helpers";
import type { Conversation, EvalRun, EvalScenario, Locale, TraceList, TraceOperation, UseCase } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const language = (page: Page) => page.getByRole("combobox", { name: /^(Language|Taal)$/ });
const sourceText = "SYNTHETIC user-authored content: keep 0,25 and 0.25 unchanged.";
const scenario: EvalScenario = {
  name: "synthetic_scenario", category: "edge_case", description: "Synthetic description",
  input_message: sourceText, expected_behavior: "SYNTHETIC expected behavior - unchanged.",
  input_data: { amount: "0,25" }, expected_tool_calls: ["synthetic_tool"], evaluators: ["task_adherence"],
};
const completedRun: EvalRun = {
  run_id: "synthetic-run", use_case: "akte-agent", mode: "validation", status: "completed",
  scenarios: [scenario.name], created_at: "2026-03-21T12:00:00Z", updated_at: "2026-03-21T12:01:00Z",
  started_by: "synthetic-operator", progress: "SYNTHETIC raw progress", error: "", foundry: null,
  results: [{
    scenario: scenario.name, query: sourceText, response: "SYNTHETIC model response - unchanged.",
    status: "completed", error: "", duration_ms: 1250,
    scores: { task_adherence: { task_adherence: 4.5, task_adherence_threshold: 3, passed: true, reason: "SYNTHETIC raw evaluator reason" } },
    tool_calls: [{
      name: "synthetic_tool", status: "completed", durationMs: 2500,
      input: { amount: "0,25" }, output: "SYNTHETIC raw tool output", source: "synthetic-source",
    }],
  }],
};
const operation: TraceOperation = {
  operation_id: "synthetic-operation", timestamp: "2026-03-21T12:00:00Z", total_duration_ms: 1250,
  span_count: 2, use_case: "akte-agent", conversation_id: "synthetic-conversation", eval_run_id: "synthetic-run",
  spans: [
    { id: "synthetic-llm", parent_id: "", name: "synthetic_model_call", duration_ms: 1250, offset_ms: 0,
      timestamp: "2026-03-21T12:00:00Z", success: true, result_code: "200", type: "dependency",
      cloud_role: "synthetic-role", category: "llm", depth: 0,
      attributes: { "gen_ai.response.model": "synthetic-model", "gen_ai.usage.input_tokens": 1234, "gen_ai.usage.output_tokens": 56, "raw_attribute": sourceText } },
    { id: "synthetic-tool", parent_id: "synthetic-llm", name: "synthetic_tool_call", duration_ms: 250,
      offset_ms: 200, timestamp: "2026-03-21T12:00:00Z", success: true, result_code: "200",
      type: "dependency", cloud_role: "synthetic-role", category: "tool", depth: 1,
      attributes: { "gen_ai.tool.name": "synthetic_tool", "kratos.eval_run_id": "synthetic-run" } },
  ],
  logs: [{ timestamp: "2026-03-21T12:00:00Z", message: "SYNTHETIC raw log - unchanged", severity: 100, cloud_role: "synthetic-role" }],
};

async function fixture(page: Page) {
  const catalog: UseCase[] = [{
    name: "akte-agent", displayName: "Akte Agent", description: "Synthetic", sampleQuestions: [],
    skillCount: 0, curated: true,
    localizations: { en: { displayName: "Akte Agent EN" }, nl: { displayName: "Akte Agent NL" } },
  }, {
    name: "synthetic-import", displayName: "Imported source label", description: "Synthetic imported metadata",
    sampleQuestions: [], skillCount: 0, curated: false,
  }];
  const state = {
    catalog, scenarios: [] as EvalScenario[], runs: [] as EvalRun[],
    conversations: [] as Conversation[],
    traces: { operations: [operation], summary: { total_operations: 1, avg_latency_ms: 1250, total_tokens: 1290, models_used: ["synthetic-model"], error: "" } } satisfies TraceList,
    fail: "", failureStatus: 500, generation: [] as unknown[], starts: [] as unknown[],
    saves: [] as EvalScenario[], traceQueries: [] as URLSearchParams[], detailQueries: [] as URLSearchParams[],
    paths: [] as string[], pauseGeneration: null as Promise<void> | null, pauseRun: null as Promise<void> | null,
    generatedScenarios: [scenario],
    failSaveName: "", saveAttempts: [] as string[],
  };
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname.slice(mount.length);
    if (path === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!path.startsWith("/api/")) return route.continue();
    state.paths.push(url.pathname);
    const fail = () => route.fulfill({ status: state.failureStatus, json: { detail: "SENSITIVE_SYNTHETIC_DIAGNOSTIC" } });
    if (path === "/api/use-cases") return route.fulfill({ json: { useCases: state.catalog } });
    if (path === "/api/models") return route.fulfill({ json: { models: [] } });
    if (path === "/api/conversations") return route.fulfill({ json: { conversations: state.conversations } });
    if (path.endsWith("/messages")) return route.fulfill({ json: [] });
    if (path === "/api/admin/skills") return route.fulfill({ json: { skills: [] } });
    if (path === "/api/admin/mcp-servers") return route.fulfill({ json: { servers: {} } });
    if (path === "/api/admin/system-prompt") return route.fulfill({ json: { content: sourceText, isDefault: false } });
    if (path.endsWith("/evals/scenarios/generate")) {
      state.generation.push(req.postDataJSON());
      if (state.pauseGeneration) await state.pauseGeneration;
      if (state.fail === "generate") return fail();
      return route.fulfill({ json: { scenarios: state.generatedScenarios, persisted: false } });
    }
    if (path.endsWith("/evals/scenarios")) {
      if (state.fail === "retired") return route.fulfill({
        status: 410, json: { detail: { code: "PERSONA_UNAVAILABLE" } },
      });
      if (state.fail === "load") return fail();
      if (state.fail === "malformed") return route.fulfill({ json: {} });
      return route.fulfill({ json: { scenarios: state.scenarios } });
    }
    if (path.includes("/evals/scenarios/")) {
      if (req.method() === "PUT") {
        state.saveAttempts.push(req.postDataJSON().name);
        if (req.postDataJSON().name === state.failSaveName) return fail();
      }
      if (state.fail === "save" || state.fail === "delete") return fail();
      if (req.method() === "DELETE") {
        state.scenarios = [];
        return route.fulfill({ json: {} });
      }
      const saved: EvalScenario = req.postDataJSON();
      state.saves.push(saved);
      state.scenarios = [...state.scenarios.filter((s) => s.name !== saved.name), saved];
      return route.fulfill({ json: saved });
    }
    if (path.endsWith("/evals/runs")) return route.fulfill({ json: { runs: state.runs } });
    if (path.endsWith("/evals/run")) {
      state.starts.push(req.postDataJSON());
      if (state.pauseRun) await state.pauseRun;
      if (state.fail === "start") return fail();
      const run = { ...completedRun, status: "invoking", results: [] } satisfies EvalRun;
      state.runs = [run];
      return route.fulfill({ json: run });
    }
    if (path.includes("/evals/runs/")) {
      if (state.fail === "poll") return fail();
      state.runs = [completedRun];
      return route.fulfill({ json: completedRun });
    }
    if (path === "/api/traces/operations") {
      state.traceQueries.push(url.searchParams);
      if (state.fail === "traces") return fail();
      return route.fulfill({ json: state.traces });
    }
    if (path.startsWith("/api/traces/operations/")) {
      state.detailQueries.push(url.searchParams);
      if (state.fail === "detail") return fail();
      return route.fulfill({ json: operation });
    }
    throw new Error(`Unexpected synthetic request: ${req.method()} ${path}`);
  });
  return state;
}

async function openPanel(page: Page, locale: Locale, panel = "Evals", embedded = false) {
  await page.addInitScript((locale) => localStorage.setItem("kratos.locale", locale), locale);
  await page.goto(`${FRONTEND_URL}/${embedded ? "?embed=1" : ""}`);
  await expect(language(page)).toHaveValue(locale);
  await page.getByRole("button", { name: ui[locale].manager, exact: true }).click();
  // Manager navigation belongs to #20; tolerate its translated label after integration.
  await page.getByRole("button", { name: panel === "Evals" ? /^(Evals|Evaluations|Evaluaties)$/ : /^Traces$/, exact: true }).click();
}

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: retired scenarios do not hide saved evaluation results`, async ({ page }) => {
    const state = await fixture(page);
    state.fail = "retired";
    state.runs = [{ ...completedRun, use_case: "insurance" }];
    state.conversations = [{
      id: "synthetic-retired", title: "Retired evaluation history", useCase: "insurance",
      status: "active", createdAt: completedRun.created_at, updatedAt: completedRun.updated_at,
    }];
    await page.addInitScript((locale) => localStorage.setItem("kratos.locale", locale), locale);
    await page.goto(`${FRONTEND_URL}/`);
    await page.getByRole("button", { name: /Retired evaluation history/ }).first().click();
    await page.getByRole("button", { name: ui[locale].manager, exact: true }).click();
    await page.getByRole("button", { name: /^(Evals|Evaluations|Evaluaties)$/, exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "PERSONA_UNAVAILABLE" })).toBeVisible();
    await expect(page.getByText(ui[locale]["eval.latestRun"], { exact: true })).toBeVisible();
    await expect(page.getByText(ui[locale]["eval.persona"].replace("{name}", "insurance"), { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: ui[locale]["eval.generate"], exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: ui[locale]["eval.runValidation"], exact: true })).toBeDisabled();
    expect(state.starts).toEqual([]);
    expect(state.generation).toEqual([]);
    expect(state.runs[0].results).toEqual(completedRun.results);
  });
}

for (const locale of ["en", "nl"] as const) {
  const other = locale === "en" ? "nl" : "en";
  test(`${locale}: generate/cancel, review and edit preserve source text and unsaved inputs across language changes`, async ({ page }) => {
    const state = await fixture(page);
    await openPanel(page, locale);
    await page.getByRole("button", { name: ui[locale]["eval.generate"], exact: true }).first().click();
    let dialog = page.getByRole("dialog", { name: ui[locale]["eval.generate"] });
    await dialog.getByRole("button", { name: ui[locale].cancel, exact: true }).last().click();
    expect(state.generation).toHaveLength(0);
    await page.getByRole("button", { name: ui[locale]["eval.generate"], exact: true }).first().click();
    await dialog.getByRole("textbox", { name: ui[locale]["eval.instructions"], exact: true }).fill(sourceText);
    await dialog.getByRole("spinbutton", { name: ui[locale]["eval.count"] }).fill("2");
    await language(page).selectOption(other);
    dialog = page.getByRole("dialog", { name: ui[other]["eval.generate"] });
    await expect(dialog.getByRole("textbox", { name: ui[other]["eval.instructions"], exact: true })).toHaveValue(sourceText);
    await expect(dialog.getByRole("spinbutton", { name: ui[other]["eval.count"] })).toHaveValue("2");
    await expect(dialog).toContainText(state.catalog[0].localizations![other]!.displayName!);
    let release!: () => void;
    state.pauseGeneration = new Promise<void>((resolve) => { release = resolve; });
    await dialog.getByRole("button", { name: ui[other]["eval.generateDraft"] }).click();
    await expect.poll(() => state.generation.length).toBe(1);
    await language(page).selectOption(locale);
    dialog = page.getByRole("dialog", { name: ui[locale]["eval.generate"] });
    await expect(dialog.getByRole("button", { name: ui[locale]["eval.generating"] })).toBeDisabled();
    release();
    await expect(dialog.getByRole("textbox", { name: ui[locale]["eval.inputMessage"], exact: true })).toHaveValue(sourceText);
    await dialog.getByRole("textbox", { name: ui[locale]["eval.scenarioName"] }).fill("synthetic_reviewed");
    await dialog.getByRole("button", { name: ui[locale]["eval.saveAll"].replace("{count}", "1") }).click();
    await expect(page.getByText("synthetic_reviewed", { exact: true })).toBeVisible();
    expect(state.generation).toEqual([{ count: 2, instructions: sourceText, persist: false }]);
    expect(state.saves[0]).toEqual({ ...scenario, name: "synthetic_reviewed" });
    await page.getByRole("button", { name: ui[locale]["eval.edit"], exact: true }).click();
    const edit = page.getByRole("dialog", { name: ui[locale]["eval.editScenario"] });
    await edit.getByRole("textbox", { name: ui[locale]["eval.inputMessage"], exact: true }).fill("SYNTHETIC unsaved edit");
    await language(page).selectOption(other);
    await expect(page.getByRole("textbox", { name: ui[other]["eval.inputMessage"], exact: true })).toHaveValue("SYNTHETIC unsaved edit");
    await expect(page.getByRole("combobox", { name: ui[other]["eval.category"], exact: true })).toHaveValue("edge_case");
    await page.getByRole("dialog").getByRole("button", { name: ui[other].cancel, exact: true }).last().click();
    expect(state.saves).toHaveLength(1);
    expect(state.paths.every((path) => path.startsWith(`${mount}/api/`))).toBe(true);
  });

  test(`${locale}: starting, polling and viewing a run survives switching without duplicate execution`, async ({ page }) => {
    const state = await fixture(page);
    state.scenarios = [scenario];
    await page.clock.install();
    await openPanel(page, locale);
    let release!: () => void;
    state.pauseRun = new Promise<void>((resolve) => { release = resolve; });
    await page.getByRole("button", { name: ui[locale]["eval.runValidation"], exact: true }).click();
    await expect.poll(() => state.starts.length).toBe(1);
    await language(page).selectOption(other);
    await expect(page.getByRole("button", { name: ui[other]["eval.runValidation"], exact: true })).toBeDisabled();
    release();
    await expect(page.getByText(ui[other]["eval.status.invoking"], { exact: true }).first()).toBeVisible();
    await page.clock.fastForward(11000);
    await expect(page.getByText(ui[other]["eval.status.completed"], { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: /synthetic_scenario/ }).click();
    await expect(page.getByText(completedRun.results[0].response, { exact: true })).toBeVisible();
    await expect(page.getByText("SYNTHETIC raw tool output", { exact: true })).toBeVisible();
    await expect(page.getByText("task_adherence", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(other === "nl" ? "4,5/3" : "4.5/3", { exact: true })).toBeVisible();
    await expect(page.getByText(other === "nl" ? "1,3 sec" : "1.3 secs", { exact: true })).toBeVisible();
    await expect(page.getByText(other === "nl" ? /21 mrt 2026/ : /21 Mar 2026/).first()).toBeVisible();
    await language(page).selectOption(locale);
    await expect(page.getByText(completedRun.results[0].response, { exact: true })).toBeVisible();
    expect(state.starts).toEqual([{ mode: "validation", scenarios: [] }]);
    await page.getByRole("button", { name: ui[locale]["eval.runFoundry"], exact: true }).click();
    expect(state.starts).toEqual([{ mode: "validation", scenarios: [] }, { mode: "foundry", scenarios: [] }]);
  });

  test(`${locale}: trace selection, category filters, lookback and raw diagnostics survive switch and refresh`, async ({ page }) => {
    const state = await fixture(page);
    await openPanel(page, locale, "Traces", true);
    await page.getByRole("textbox", { name: ui[locale]["trace.conversationId"], exact: true }).fill("synthetic-conversation");
    await page.getByRole("textbox", { name: ui[locale]["trace.evalRunId"], exact: true }).fill("synthetic-run");
    await page.getByRole("spinbutton", { name: ui[locale]["trace.lookback"], exact: true }).fill("48");
    await expect.poll(() => state.traceQueries.at(-1)?.get("hours")).toBe("48");
    const row = page.getByRole("button", { name: /synthetic-operati/ });
    await row.click();
    await page.getByRole("button", { name: /synthetic_model_call/ }).click();
    await expect(page.getByText(sourceText, { exact: true })).toBeVisible();
    await expect(page.getByText("SYNTHETIC raw log - unchanged", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(`${ui[locale]["trace.category.llm"]}.*1`) }).click();
    await expect(page.getByRole("button", { name: /synthetic_tool_call/ })).toHaveCount(0);
    const queries = state.traceQueries.length;
    const details = state.detailQueries.length;
    await language(page).selectOption(other);
    await expect(page.getByRole("textbox", { name: ui[other]["trace.conversationId"], exact: true })).toHaveValue("synthetic-conversation");
    await expect(page.getByRole("textbox", { name: ui[other]["trace.evalRunId"], exact: true })).toHaveValue("synthetic-run");
    await expect(page.getByRole("spinbutton", { name: ui[other]["trace.lookback"], exact: true })).toHaveValue("48");
    await expect(page.getByRole("button", { name: new RegExp(`${ui[other]["trace.category.llm"]}.*1`) })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText(sourceText, { exact: true })).toBeVisible();
    expect(state.traceQueries).toHaveLength(queries);
    expect(state.detailQueries).toHaveLength(details);
    await page.getByRole("button", { name: ui[other]["trace.refresh"], exact: true }).click();
    await expect.poll(() => state.traceQueries.length).toBe(queries + 1);
    await expect(row).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText(sourceText, { exact: true })).toBeVisible();
    await expect(page.getByText(other === "nl" ? "1.290" : "1,290", { exact: true })).toBeVisible();
    expect(Object.fromEntries(state.traceQueries.at(-1)!)).toEqual({
      use_case: "akte-agent", conversation_id: "synthetic-conversation", eval_run_id: "synthetic-run", hours: "48",
    });
    expect(state.detailQueries[0].get("hours")).toBe("48");
    expect(state.paths.every((path) => path.startsWith(`${mount}/api/`))).toBe(true);
  });

  test(`${locale}: validation, empty generation and API failures keep drafts and show safe translated guidance`, async ({ page }) => {
    const state = await fixture(page);
    await openPanel(page, locale);
    await expect(page.getByText(ui[locale]["eval.noScenarios"], { exact: true })).toBeVisible();
    await expect(page.getByText(ui[locale]["eval.noRuns"], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: ui[locale]["eval.generate"], exact: true }).first().click();
    let dialog = page.getByRole("dialog", { name: ui[locale]["eval.generate"] });
    await dialog.getByRole("spinbutton", { name: ui[locale]["eval.count"] }).fill("51");
    await expect(dialog.getByText(ui[locale]["eval.countValidation"])).toBeVisible();
    await expect(dialog.getByRole("button", { name: ui[locale]["eval.generateDraft"] })).toBeDisabled();
    await dialog.getByRole("spinbutton", { name: ui[locale]["eval.count"] }).fill("1");
    await dialog.getByRole("textbox", { name: ui[locale]["eval.instructions"], exact: true }).fill(sourceText);
    state.fail = "generate";
    state.failureStatus = 403;
    await dialog.getByRole("button", { name: ui[locale]["eval.generateDraft"] }).click();
    await expect(dialog.getByRole("alert")).toContainText(ui[locale]["error.AUTH_REQUIRED"]);
    await language(page).selectOption(other);
    dialog = page.getByRole("dialog", { name: ui[other]["eval.generate"] });
    await expect(dialog.getByRole("alert")).toContainText(ui[other]["error.AUTH_REQUIRED"]);
    await expect(dialog.getByRole("textbox", { name: ui[other]["eval.instructions"], exact: true })).toHaveValue(sourceText);
    await expect(page.getByText("SENSITIVE_SYNTHETIC_DIAGNOSTIC")).toHaveCount(0);
    state.fail = "";
    state.generatedScenarios = [];
    await dialog.getByRole("button", { name: ui[other]["eval.generateDraft"] }).click();
    await expect(dialog.getByRole("status")).toContainText(ui[other]["eval.noDrafts"]);
    state.generatedScenarios = [scenario];
    await dialog.getByRole("button", { name: ui[other]["eval.generateDraft"] }).click();
    await dialog.getByRole("textbox", { name: ui[other]["eval.scenarioName"] }).fill("");
    await expect(dialog.getByRole("button", { name: ui[other]["eval.saveAll"].replace("{count}", "1") })).toBeDisabled();
    await expect(dialog.getByText(ui[other]["eval.validation"])).toBeVisible();
    await dialog.getByRole("textbox", { name: ui[other]["eval.scenarioName"] }).fill(scenario.name);
    state.fail = "save";
    state.failureStatus = 500;
    await dialog.getByRole("button", { name: ui[other]["eval.saveAll"].replace("{count}", "1") }).click();
    await expect(dialog.getByRole("alert")).toContainText(ui[other]["error.EVAL_SAVE"]);
    await expect(dialog.getByRole("textbox", { name: ui[other]["eval.inputMessage"], exact: true })).toHaveValue(sourceText);
    await expect(page.getByText(ui[other]["eval.saved"], { exact: true })).toHaveCount(0);
    state.fail = "";
    await dialog.getByRole("button", { name: ui[other]["eval.saveAll"].replace("{count}", "1") }).click();
    await page.getByRole("button", { name: ui[other]["eval.delete"], exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText(scenario.name);
    await language(page).selectOption(locale);
    await page.getByRole("dialog").getByRole("button", { name: ui[locale].cancel, exact: true }).click();
    expect(state.scenarios).toHaveLength(1);
    await page.getByRole("button", { name: ui[locale]["eval.delete"], exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: ui[locale].delete, exact: true }).click();
    await expect(page.getByText(ui[locale]["eval.deleted"], { exact: true })).toBeVisible();
    expect(state.scenarios).toHaveLength(0);
  });

  test(`${locale}: loading and polling failures never appear as successful evaluations`, async ({ page }) => {
    const state = await fixture(page);
    state.fail = "malformed";
    await page.clock.install();
    await openPanel(page, locale);
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_LOAD" })).toContainText(ui[locale]["error.EVAL_LOAD"]);
    await expect(page.getByText(ui[locale]["eval.noScenarios"], { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: ui[locale].dismiss, exact: true }).click();
    await expect(page.getByText(ui[locale]["eval.noScenarios"], { exact: true })).toHaveCount(0);
    state.fail = "start";
    await page.getByRole("button", { name: ui[locale]["trace.refresh"], exact: true }).click();
    await expect(page.getByText(ui[locale]["eval.noScenarios"], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: ui[locale]["eval.runValidation"], exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_START" })).toContainText(ui[locale]["error.EVAL_START"]);
    await expect(page.getByText(ui[locale]["eval.status.completed"], { exact: true })).toHaveCount(0);
    state.fail = "";
    await page.getByRole("button", { name: ui[locale]["eval.runValidation"], exact: true }).click();
    await expect(page.getByText(ui[locale]["eval.status.invoking"], { exact: true }).first()).toBeVisible();
    state.fail = "poll";
    await page.clock.fastForward(11000);
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_POLL" })).toContainText(ui[locale]["error.EVAL_POLL"]);
    await language(page).selectOption(other);
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_POLL" })).toContainText(ui[other]["error.EVAL_POLL"]);
    state.fail = "";
    await page.clock.fastForward(11000);
    await expect(page.getByText(ui[other]["eval.status.completed"], { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_POLL" })).toHaveCount(0);
    await expect(page.getByText("SENSITIVE_SYNTHETIC_DIAGNOSTIC")).toHaveCount(0);
    expect(state.starts).toHaveLength(2);
  });

  test(`${locale}: trace empty, failed list, unavailable summary and failed detail remain distinct`, async ({ page }) => {
    const state = await fixture(page);
    state.traces.operations = [];
    await openPanel(page, locale, "Traces");
    await expect(page.getByText(ui[locale]["trace.noTraces"], { exact: true })).toBeVisible();
    state.fail = "traces";
    await page.getByRole("button", { name: ui[locale]["trace.refresh"], exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "TRACE_LOAD" })).toContainText(ui[locale]["error.TRACE_LOAD"]);
    await expect(page.getByText(ui[locale]["trace.noTraces"], { exact: true })).toHaveCount(0);
    state.fail = "";
    state.traces.summary.error = "SENSITIVE_SYNTHETIC_DIAGNOSTIC";
    await page.getByRole("button", { name: ui[locale]["trace.refresh"], exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "TRACE_UNAVAILABLE" })).toContainText(ui[locale]["error.TRACE_UNAVAILABLE"]);
    await expect(page.getByText(ui[locale]["trace.noTraces"], { exact: true })).toHaveCount(0);
    state.traces.summary.error = "";
    state.traces.operations = [operation];
    state.fail = "detail";
    await page.getByRole("button", { name: ui[locale]["trace.refresh"], exact: true }).click();
    const row = page.getByRole("button", { name: /synthetic-operation/ });
    await row.click();
    await expect(page.getByRole("alert").filter({ hasText: "TRACE_DETAIL" })).toContainText(ui[locale]["error.TRACE_DETAIL"]);
    await expect(page.getByRole("button", { name: /synthetic_model_call/ })).toHaveCount(0);
    await language(page).selectOption(other);
    await expect(page.getByRole("alert").filter({ hasText: "TRACE_DETAIL" })).toContainText(ui[other]["error.TRACE_DETAIL"]);
    state.fail = "";
    await row.click();
    await row.click();
    await expect(page.getByRole("button", { name: /synthetic_model_call/ })).toBeVisible();
    await expect(page.getByText("SENSITIVE_SYNTHETIC_DIAGNOSTIC")).toHaveCount(0);
  });

  test(`${locale}: imported persona fallback and selected historical run remain unchanged`, async ({ page }) => {
    const state = await fixture(page);
    const historical: EvalRun = {
      ...completedRun, run_id: "synthetic-old-run", created_at: "2020-03-21T12:00:00Z",
      results: [{ ...completedRun.results[0], scenario: "synthetic_historical_scenario", error: "SYNTHETIC raw evaluation diagnostic" }],
    };
    state.runs = [completedRun, historical];
    await openPanel(page, locale);
    await page.getByRole("combobox", { name: ui[locale].selectPersona, exact: true }).last().selectOption("synthetic-import");
    await expect(page.getByText("Persona: Imported source label", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /2020/ }).click();
    await page.getByRole("button", { name: /synthetic_historical_scenario/ }).click();
    const requests = state.paths.length;
    await language(page).selectOption(other);
    await expect(page.getByRole("combobox", { name: ui[other].selectPersona, exact: true }).last()).toHaveValue("synthetic-import");
    await expect(page.getByText("Persona: Imported source label", { exact: true })).toBeVisible();
    await expect(page.getByText(completedRun.results[0].response, { exact: true })).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "EVAL_FAILED" })).toContainText(ui[other]["error.EVAL_FAILED"]);
    await expect(page.getByText("SYNTHETIC raw evaluation diagnostic", { exact: true })).not.toBeVisible();
    await page.getByRole("group").filter({ hasText: "SYNTHETIC raw evaluation diagnostic" }).getByText(ui[other]["eval.rawDiagnostics"], { exact: true }).click();
    await expect(page.getByText("SYNTHETIC raw evaluation diagnostic", { exact: true })).toBeVisible();
    expect(state.paths).toHaveLength(requests);
    expect(state.starts).toHaveLength(0);
  });

  test(`${locale}: partial save keeps remaining drafts and retries only unsaved scenarios`, async ({ page }) => {
    const state = await fixture(page);
    state.generatedScenarios = [scenario, { ...scenario, name: "synthetic_second" }];
    state.failSaveName = "synthetic_second";
    await openPanel(page, locale);
    await page.getByRole("button", { name: ui[locale]["eval.generate"], exact: true }).first().click();
    let dialog = page.getByRole("dialog", { name: ui[locale]["eval.generate"] });
    await dialog.getByRole("button", { name: ui[locale]["eval.generateDraft"] }).click();
    await dialog.getByRole("button", { name: ui[locale]["eval.saveAll"].replace("{count}", "2") }).click();
    await expect(dialog.getByRole("alert")).toContainText(ui[locale]["error.EVAL_SAVE"]);
    await expect(dialog.getByRole("textbox", { name: ui[locale]["eval.scenarioName"] })).toHaveValue("synthetic_second");
    await language(page).selectOption(other);
    dialog = page.getByRole("dialog", { name: ui[other]["eval.generate"] });
    await expect(dialog.getByRole("textbox", { name: ui[other]["eval.inputMessage"], exact: true })).toHaveValue(sourceText);
    state.failSaveName = "";
    await dialog.getByRole("button", { name: ui[other]["eval.saveAll"].replace("{count}", "1") }).click();
    await expect(page.getByText(scenario.name, { exact: true })).toBeVisible();
    await expect(page.getByText("synthetic_second", { exact: true })).toBeVisible();
    expect(state.saveAttempts).toEqual([scenario.name, "synthetic_second", "synthetic_second"]);
    expect(state.scenarios).toEqual(state.generatedScenarios);
  });
}
