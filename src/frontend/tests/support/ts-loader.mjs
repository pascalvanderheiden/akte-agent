// Node module hooks that let tests import the app's TS/TSX sources directly.
import { access, readFile } from "node:fs/promises";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const srcRoot = pathToFileURL(resolvePath("src") + "/").href;

async function exists(url) {
  try {
    await access(fileURLToPath(url));
    return true;
  } catch {
    return false;
  }
}

export async function resolve(specifier, context, next) {
  let url;
  if (specifier.startsWith("@/")) url = new URL(specifier.slice(2), srcRoot).href;
  else if (specifier.startsWith(".") && context.parentURL?.startsWith(srcRoot)) url = new URL(specifier, context.parentURL).href;
  if (url && !/\.[cm]?[jt]sx?$/.test(url)) {
    for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
      if (await exists(url + suffix)) return { url: url + suffix, shortCircuit: true };
    }
  }
  return next(url ?? specifier, context);
}

export async function load(url, context, next) {
  if (!/\.tsx?$/.test(url)) return next(url, context);
  const fileName = fileURLToPath(url);
  const { outputText } = ts.transpileModule(await readFile(fileName, "utf8"), {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  return { format: "module", source: outputText, shortCircuit: true };
}
