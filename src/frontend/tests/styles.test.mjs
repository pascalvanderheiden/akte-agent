import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { before, test } from "node:test";
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";

let root;
before(async () => {
  const from = resolve("src/app/globals.css");
  const source = await readFile(from, "utf8");
  const result = await postcss([tailwind({ optimize: false })]).process(
    `${source}\n@source inline("bg-accent text-accent-fg font-sans font-mono shadow-card animate-fade-in dark:bg-surface rounded-xs outline-hidden prose");`,
    { from },
  );
  root = result.root;
});

function declaration(selector, property) {
  let value;
  root.walkRules(selector, (rule) => {
    rule.walkDecls(property, (decl) => {
      value = decl.value;
    });
  });
  assert.notEqual(value, undefined, `Missing ${selector} ${property}`);
  return value;
}

test("semantic utilities resolve the active theme at the element", () => {
  assert.equal(declaration(".bg-accent", "background-color"), "var(--accent)");
  assert.equal(declaration(".text-accent-fg", "color"), "var(--accent-fg)");
  assert.match(declaration(".font-sans", "font-family"), /var\(--font-display\)/);
  assert.match(declaration(".font-mono", "font-family"), /var\(--theme-font-mono\)/);
  assert.match(declaration(".shadow-card", "--tw-shadow"), /var\(--theme-shadow-card\)/);
});

test("manual dark mode, typography and animations remain generated", () => {
  const css = root.toString();
  assert.match(css, /\.dark\\:bg-surface/);
  assert.match(css, /\.dark \*/);
  assert.match(css, /\.prose\s*\{/);
  assert.match(css, /@keyframes fadeIn/);
});

test("theme aliases introduce no self-referencing custom properties", () => {
  root.walkDecls((decl) => {
    if (decl.prop.startsWith("--")) {
      assert.ok(
        !decl.value.includes(`var(${decl.prop})`),
        `Self-referencing theme token ${decl.prop}`,
      );
    }
  });
});
