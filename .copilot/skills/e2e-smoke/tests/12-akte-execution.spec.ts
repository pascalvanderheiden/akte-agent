/**
 * Real packaged stage 4/5 drafts + downloaded bytes; deterministic synthetic
 * model/transport fixtures, NOT live model or external integration proof.
 */
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, unlinkSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FRONTEND_URL } from "./helpers";
import type { Locale, UseCase } from "../../../../src/frontend/src/types";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const python = process.env.AKTE_TEST_PYTHON || path.join(root, "src/backend/.venv/bin/python");
const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const generated: string[] = [];
type Draft = { path: string; text: string };
type Fixture = { catalog: UseCase[]; drafts: Draft[] };

function makeFixture(locale: Locale): Fixture {
  const result: Fixture = JSON.parse(execFileSync(python, ["-c", `
import asyncio, copy, json, sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers.use_cases import router
from app.services.skill_registry import SkillRegistry
root = Path("../../use-cases").resolve()
sys.path.insert(0, str(root / "akte-agent/skills/working-artifacts/scripts"))
from artifact import write_artifact
from execution_record import prepare
from reconciliation import calculate, as_artifact
data = json.loads((root / "akte-agent/skills/execution-preparation/references/synthetic-preparation.json").read_text())[sys.argv[1]]
async def main():
    app = FastAPI()
    app.include_router(router, prefix="/api/use-cases")
    app.state.registries = {}
    for directory in sorted(root.iterdir()):
        if (directory / "SYSTEM_PROMPT.md").is_file():
            registry = SkillRegistry()
            await registry.load(directory.name, local_root=str(root))
            app.state.registries[directory.name] = registry
    catalog = TestClient(app).get("/api/use-cases").json()["useCases"]
    corrected = copy.deepcopy(data["reconciliation"])
    source = "SYNTHETIC correction D"
    corrected["sources"].append({"reference": source, "kind": "user_observation"})
    corrected["entries"][1].update(decision="exclude", confirmation=source)
    corrected["entries"].append({
        **corrected["entries"][1], "id": "replacement", "correction_of": "fee",
        "decision": "count", "source": source,
        "amount": "350.005" if sys.argv[1] == "en" else "350,005",
        "decimal_separator": "." if sys.argv[1] == "en" else ",",
    })
    drafts = []
    for document in (prepare(data["execution"]), as_artifact(calculate(data["reconciliation"])), as_artifact(calculate(corrected))):
        artifact = write_artifact(document)
        drafts.append({"path": artifact["path"], "text": Path(artifact["path"]).read_text()})
    print(json.dumps({"catalog": catalog, "drafts": drafts}))
asyncio.run(main())
`, locale], { cwd: path.join(root, "src/backend"), encoding: "utf8" }));
  generated.push(...result.drafts.map((draft) => draft.path));
  return result;
}

test.afterAll(() => { for (const filename of generated) unlinkSync(filename); });

async function routeFixture(page: Page, fixture: Fixture) {
  const state = {
    calls: [] as { locale: Locale; useCase: string; conversationId: string; message: string }[],
    failDownload: false, failGeneration: false,
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const pathname = url.pathname.slice(mount.length);
    if (pathname === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!pathname.startsWith("/api/")) return route.continue();
    if (pathname === "/api/use-cases") return route.fulfill({ json: { useCases: fixture.catalog } });
    if (pathname === "/api/admin/skills") return route.fulfill({ json: { skills: [] } });
    if (pathname === "/api/admin/mcp-servers") return route.fulfill({ json: { servers: {} } });
    if (pathname === "/api/conversations") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        return route.fulfill({ status: 201, json: {
          id: "synthetic-execution", title: body.title, useCase: body.useCase, status: "active",
          createdAt: "2026-01-15T12:00:00Z", updatedAt: "2026-01-15T12:00:00Z",
        } });
      }
      return route.fulfill({ json: { conversations: [] } });
    }
    if (pathname.endsWith("/messages")) return route.fulfill({ json: [] });
    if (pathname === "/api/conversations/synthetic-execution" && request.method() === "PATCH") {
      return route.fulfill({ json: {} });
    }
    if (pathname === "/api/agent/chat") {
      const body = request.postDataJSON();
      state.calls.push(body);
      const draft = fixture.drafts[state.calls.length - 1];
      const content = state.failGeneration
        ? (body.locale === "en" ? "Generation failed; no downloadable worksheet available." : "Aanmaken mislukt; geen downloadbaar werkblad beschikbaar.")
        : `${draft.text}\n\n${draft.path}`;
      const events = [{ type: "content", content }, { type: "done" }];
      return route.fulfill({
        contentType: "text/event-stream",
        body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
      });
    }
    if (pathname.startsWith("/api/files/download/")) {
      if (state.failDownload) return route.fulfill({ status: 404, json: { code: "DOWNLOAD_ERROR" } });
      const filename = url.searchParams.get("path")!;
      expect(fixture.drafts.map((draft) => draft.path)).toContain(filename);
      return route.fulfill({ contentType: "text/markdown", body: readFileSync(filename) });
    }
    throw new Error(`Unexpected API request: ${request.method()} ${pathname}`);
  });
  return state;
}

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: stage 4 direct entry, stage 5 continuation and corrected downloaded bytes`, async ({ page }) => {
    const fixture = makeFixture(locale);
    const state = await routeFixture(page, fixture);
    await page.addInitScript((value) => localStorage.setItem("kratos.locale", value), locale);
    await page.goto(`${FRONTEND_URL}/`);
    const selector = page.getByRole("combobox", { name: ui[locale].selectPersona });
    await expect(selector).toHaveValue("generic");
    expect(fixture.catalog.filter((persona) => persona.curated).map((persona) => persona.name)).toContain("akte-agent");
    await selector.selectOption("akte-agent");
    const prompts = locale === "en"
      ? ["SYNTHETIC-AKTE-EXEC: prepare identity and signing checklist from supplied deed and observations.", "Continue same dossier: reconcile supplied funds, charges and taxes.", "Correction D: exclude fee, include replacement 350.005 with confirmed decimal point; preserve originals."]
      : ["SYNTHETIC-AKTE-EXEC: bereid identiteits- en passeerchecklist voor uit akte en observaties.", "Zelfde dossier: reconcilieer aangeleverde clientgelden, kosten en belastingen.", "Correctie D: sluit kosten uit, tel vervanging 350,005 met bevestigde decimale komma mee; behoud origineel."];
    for (const [index, draft] of fixture.drafts.entries()) {
      await page.getByRole("textbox", { name: ui[locale].ask }).fill(prompts[index]);
      await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
      const link = page.getByRole("link", { name: new RegExp(path.basename(draft.path)) });
      await expect(link).toBeVisible();
      expect(state.calls[index].locale).toBe(locale);
      expect(state.calls[index].useCase).toBe("akte-agent");
      expect(state.calls[index].conversationId).toBe(state.calls[0].conversationId);
      const pending = page.waitForEvent("download");
      await link.click();
      const download = await pending;
      const chunks: Buffer[] = [];
      for await (const chunk of (await download.createReadStream())!) chunks.push(Buffer.from(chunk));
      const text = Buffer.concat(chunks).toString("utf8");
      expect(text).toBe(draft.text);
      expect(text).toContain("SYNTHETIC-AKTE-EXEC");
      expect(text).toContain(locale === "en" ? "Status: DRAFT" : "Status: CONCEPT");
      expect(text).toContain(locale === "en" ? "Temporary working download" : "Tijdelijke werkdownload");
      if (index === 0) {
        for (const expected of locale === "en"
          ? ["Supplied observations", "Agent-proposed questions", "Possible coercion", "Uncertain capacity", "Missing approval", "scanner", "Reported unsigned"]
          : ["Aangeleverde observaties", "Door agent voorgestelde vragen", "Mogelijke dwang", "Onzekere wilsbekwaamheid", "Ontbrekende goedkeuring", "scanner", "Volgens bron niet ondertekend"]) {
          expect(text).toContain(expected);
        }
        expect(text).toContain("vruchtgebruik");
        expect(text).toContain(locale === "en" ? "SYNTHETIC appointment B" : "SYNTHETIC afspraak B");
      } else {
        expect(text).toContain("ROUND_HALF_UP");
        expect(text).toContain(locale === "en" ? "not office revenue" : "geen kantooromzet");
        expect(text).toContain(locale === "en" ? "Missing supplied payment confirmation" : "Aangeleverde betaalbevestiging ontbreekt");
        expect(text).toContain(index === 1
          ? (locale === "en" ? "EUR 0.00" : "EUR 0,00")
          : (locale === "en" ? "EUR -0.01" : "EUR -0,01"));
        if (index === 2) {
          expect(text).toContain(locale === "en" ? "350.005" : "350,005");
          expect(text).toContain(locale === "en" ? "350.00" : "350,00");
          expect(text).toContain("SYNTHETIC correction D");
          expect(text).toContain(locale === "en" ? "Exclude" : "Uitsluiten");
        }
      }
    }
    state.failDownload = true;
    await page.getByRole("link", { name: new RegExp(path.basename(fixture.drafts[2].path)) }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DOWNLOAD_ERROR"] })).toBeVisible();
    state.failGeneration = true;
    const count = await page.getByRole("link", { name: /akte-draft-/ }).count();
    await page.getByRole("textbox", { name: ui[locale].ask }).fill(locale === "en" ? "Make another worksheet" : "Maak nog een werkblad");
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByText(locale === "en"
      ? "Generation failed; no downloadable worksheet available."
      : "Aanmaken mislukt; geen downloadbaar werkblad beschikbaar.")).toBeVisible();
    expect(await page.getByRole("link", { name: /akte-draft-/ }).count()).toBe(count);
  });
}
