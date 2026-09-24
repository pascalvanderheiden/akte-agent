/** Real packaged catalog/scripts; callers explicitly fixture the model/transport. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { UseCase } from "../../../../src/frontend/src/types";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const python = process.env.AKTE_TEST_PYTHON || path.join(root, "src/backend/.venv/bin/python");

export function runPython(code: string, input: unknown = null) {
  return JSON.parse(execFileSync(python, ["-c", code], {
    cwd: path.join(root, "src/backend"),
    input: JSON.stringify(input), encoding: "utf8",
  }));
}

export function loadCatalog(): UseCase[] {
  return runPython(`
import asyncio, json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers.use_cases import router
from app.services.skill_registry import SkillRegistry
async def main():
    app = FastAPI()
    app.include_router(router, prefix="/api/use-cases")
    app.state.registries = {}
    root = Path("../../use-cases").resolve()
    for directory in sorted(root.iterdir()):
        if (directory / "SYSTEM_PROMPT.md").is_file():
            registry = SkillRegistry()
            await registry.load(directory.name, local_root=str(root))
            app.state.registries[directory.name] = registry
    print(json.dumps(TestClient(app).get("/api/use-cases").json()["useCases"]))
asyncio.run(main())
`);
}
