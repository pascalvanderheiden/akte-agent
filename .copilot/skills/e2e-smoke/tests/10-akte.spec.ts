/**
 * #22 browser acceptance: real catalog + packaged calculation/artifact scripts.
 * Model/transport are deterministic synthetic fixtures, NOT live agent validation.
 * Requires the backend Python environment; no Azure services or credentials.
 */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync, unlinkSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { FRONTEND_URL } from "./helpers";
import { loadCatalog, runPython } from "./akte-fixtures";
import type { Locale } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const generated: string[] = [];

function makeFixture(locale: Locale) {
  const catalog = loadCatalog();
  const body = locale === "en"
    ? "## Client wishes\nDiscuss a Netherlands will.\n## Family circumstances\nUnknown family status. Two children in note A; one in note B: contradiction.\n## Business goals\nUnknown.\n## Matter type\nWill; Netherlands.\n## Evidence\nSYNTHETIC notes A/B; not independently verified.\n## Assumptions\nNone established.\n## Open questions\nClarify children and family status; human review required.\n## Missing documents\nRequest existing testamentary documents through approved channel."
    : "## Cliëntwensen\nBespreek een Nederlands testament.\n## Familieomstandigheden\nBurgerlijke staat onbekend. Twee kinderen in notitie A; één in B: tegenstrijdigheid.\n## Zakelijke doelen\nOnbekend.\n## Zaaktype\nTestament; Nederland.\n## Bewijs\nSYNTHETIC notities A/B; niet onafhankelijk geverifieerd.\n## Aannames\nGeen vastgesteld.\n## Open vragen\nVerduidelijk kinderen en burgerlijke staat; menselijke beoordeling vereist.\n## Ontbrekende documenten\nVraag bestaande testamentaire documenten via goedgekeurd kanaal.";
  const input = {
    dossier: "SYNTHETIC-AKTE-001", locale,
    sources: [{ reference: "SYNTHETIC notes A/B", kind: "synthetic" }],
    artifact_type: locale === "en" ? "Intake brief" : "Intakeoverzicht",
    body,
    entries: (locale === "en" ? ["Preparation", "Conversation", "Note-writing"] : ["Voorbereiding", "Gesprek", "Uitwerking"]).map((activity, index) => ({
      id: String(index), activity, date: "2026-01-15", timekeeper: "SYNTHETIC Notary A",
      hours: (locale === "en" ? ["0.25", "1.0", "0.5"] : ["0,25", "1,0", "0,5"])[index],
      source: "SYNTHETIC notes A/B",
    })),
  };
  const result: { intake: { path: string }; time: { path: string }; hours: string; minutes: string } = runPython(`
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path("../../use-cases/akte-agent/skills/working-artifacts/scripts").resolve()))
from artifact import write_artifact
from time_record import calculate, as_artifact
data = json.load(sys.stdin)
record = calculate(data)
print(json.dumps({"intake": write_artifact(data), "time": write_artifact(as_artifact(record)), "hours": record["hours_display"], "minutes": record["minutes_display"]}))
`, input);
  generated.push(result.intake.path, result.time.path);
  return { catalog, input, result, body };
}

test.afterAll(() => { for (const filename of generated) unlinkSync(filename); });

async function routeFixture(page: Page, fixture: ReturnType<typeof makeFixture>) {
  const state = { calls: [] as { locale: Locale; useCase: string; message: string; conversationId: string }[], failDownload: false, failGeneration: false };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const pathname = url.pathname.slice(mount.length);
    if (pathname === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!pathname.startsWith("/api/")) return route.continue();
    if (pathname === "/api/use-cases") return route.fulfill({ json: { useCases: fixture.catalog } });
    if (pathname === "/api/models") return route.fulfill({ json: { models: [] } });
    if (pathname === "/api/admin/skills") return route.fulfill({ json: { skills: [] } });
    if (pathname === "/api/admin/mcp-servers") return route.fulfill({ json: { servers: {} } });
    if (pathname === "/api/conversations") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        return route.fulfill({ status: 201, json: { id: "synthetic-akte", title: body.title, useCase: body.useCase, status: "active", createdAt: "2026-01-15T12:00:00Z", updatedAt: "2026-01-15T12:00:00Z" } });
      }
      return route.fulfill({ json: { conversations: [] } });
    }
    if (pathname.endsWith("/messages")) return route.fulfill({ json: [] });
    if (pathname === "/api/conversations/synthetic-akte" && request.method() === "PATCH") return route.fulfill({ json: {} });
    if (pathname === "/api/agent/chat") {
      const body = request.postDataJSON();
      state.calls.push(body);
      const english = body.locale === "en" || body.message.includes("in English");
      const followup = english
        ? "DRAFT - NOT SENT\nSubject: Missing intake information\nDear [Client],\nPlease clarify family status and the conflicting child counts, and provide existing testamentary documents via the approved office channel.\n[Notary]"
        : "CONCEPT - NIET VERZONDEN\nOnderwerp: Ontbrekende intakegegevens\nBeste [Cliënt],\nVerduidelijk uw burgerlijke staat en het tegenstrijdige aantal kinderen. Lever bestaande testamentaire documenten aan via het goedgekeurde kantoorkanaal.\n[Notaris]";
      const content = state.failGeneration
        ? (english ? "Draft text only; generation failed. No download is available." : "Alleen concepttekst; aanmaken mislukt. Geen download beschikbaar.")
        : state.calls.length === 1
          ? `${fixture.body}\n\n${fixture.result.hours} ${english ? "hours" : "uur"} / ${fixture.result.minutes} ${english ? "minutes" : "minuten"}\n\n${followup}\n\n${fixture.result.intake.path}\n\n${fixture.result.time.path}\n\n${english ? "Temporary storage; save reviewed drafts." : "Tijdelijke opslag; bewaar beoordeelde concepten."}`
          : followup;
      const events = [{ type: "content", content }, { type: "done" }];
      return route.fulfill({ contentType: "text/event-stream", body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") });
    }
    if (pathname.startsWith("/api/files/download/")) {
      if (state.failDownload) return route.fulfill({ status: 404, json: { code: "DOWNLOAD_ERROR" } });
      const filename = url.searchParams.get("path")!;
      expect([fixture.result.intake.path, fixture.result.time.path]).toContain(filename);
      return route.fulfill({ contentType: "text/markdown", body: readFileSync(filename) });
    }
    throw new Error(`Unexpected API request: ${request.method()} ${pathname}`);
  });
  return state;
}

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: Akte discovery, intake, exact time, follow-up and inspected downloads`, async ({ page }) => {
    const fixture = makeFixture(locale);
    const state = await routeFixture(page, fixture);
    await page.addInitScript((value) => localStorage.setItem("kratos.locale", value), locale);
    await page.goto(`${FRONTEND_URL}/`);
    await expect(page.getByRole("combobox", { name: ui[locale].selectPersona })).toHaveCount(0);
    expect(fixture.catalog.map((persona) => persona.name)).toEqual(["akte-agent"]);
    await expect(page.getByRole("heading", { name: "Akte Agent", exact: true })).toBeVisible();
    const akte = fixture.catalog.find((persona) => persona.name === "akte-agent")!;
    for (const starter of akte.localizations![locale]!.sampleQuestions!) {
      await expect(page.getByRole("button", { name: starter, exact: true })).toBeVisible();
    }
    await page.getByRole("textbox", { name: ui[locale].ask }).fill(JSON.stringify(fixture.input));
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByText(`${fixture.result.hours} ${locale === "en" ? "hours" : "uur"} / 105 ${locale === "en" ? "minutes" : "minuten"}`, { exact: true })).toBeVisible();
    expect(state.calls[0].locale).toBe(locale);
    expect(state.calls[0].useCase).toBe("akte-agent");
    await expect(page.getByText(locale === "en" ? /DRAFT - NOT SENT/ : /CONCEPT - NIET VERZONDEN/)).toBeVisible();
    for (const file of [fixture.result.intake.path, fixture.result.time.path]) {
      const pending = page.waitForEvent("download");
      await page.getByRole("link", { name: new RegExp(path.basename(file).replace(".", "\\.") + ".*download|Download " + path.basename(file).replace(".", "\\."), "i") }).click();
      const download = await pending;
      const chunks: Buffer[] = [];
      for await (const chunk of (await download.createReadStream())!) chunks.push(Buffer.from(chunk));
      const text = Buffer.concat(chunks).toString("utf8");
      expect(text).toBe(readFileSync(file, "utf8"));
      expect(text).toContain("SYNTHETIC-AKTE-001");
      expect(text).toContain(locale === "en" ? "Status: DRAFT" : "Status: CONCEPT");
      expect(text).toContain("SYNTHETIC notes A/B");
      if (file === fixture.result.time.path) {
        expect(text).toContain(locale === "en" ? "1.75 hours / 105 minutes" : "1,75 uur / 105 minuten");
        for (const row of fixture.input.entries) {
          expect(text).toContain(row.activity);
          expect(text).toContain(row.hours);
          expect(text).toContain(row.date);
          expect(text).toContain(row.timekeeper);
        }
      }
    }
    state.failDownload = true;
    await page.getByRole("link", { name: new RegExp(path.basename(fixture.result.time.path)) }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DOWNLOAD_ERROR"] })).toBeVisible();
    await page.getByRole("combobox", { name: /^(Language|Taal)$/ }).selectOption("nl");
    await page.getByRole("textbox", { name: nl.ask }).fill("Draft the follow-up in English");
    await page.getByRole("button", { name: nl.sendMessage, exact: true }).click();
    await expect(page.getByText(/DRAFT - NOT SENT/).last()).toBeVisible();
    expect(state.calls.at(-1)?.locale).toBe("nl");
    expect(state.calls.at(-1)?.conversationId).toBe(state.calls[0].conversationId);
    state.failGeneration = true;
    const linkCount = await page.getByRole("link", { name: /akte-draft-/ }).count();
    await page.getByRole("textbox", { name: nl.ask }).fill("Maak nog een download");
    await page.getByRole("button", { name: nl.sendMessage, exact: true }).click();
    await expect(page.getByText("Alleen concepttekst; aanmaken mislukt. Geen download beschikbaar.")).toBeVisible();
    expect(await page.getByRole("link", { name: /akte-draft-/ }).count()).toBe(linkCount);
  });
}
