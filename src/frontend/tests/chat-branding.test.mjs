import assert from "node:assert/strict";
import { register } from "node:module";
import { before, test } from "node:test";

register("./support/ts-loader.mjs", import.meta.url);

let createElement, renderToStaticMarkup, LandingHeroBranding, MobileChatHeader, LocaleProvider;
before(async () => {
  ({ createElement } = await import("react"));
  ({ renderToStaticMarkup } = await import("react-dom/server"));
  ({ LandingHeroBranding, MobileChatHeader } = await import("../src/components/ChatBranding.tsx"));
  ({ LocaleProvider } = await import("../src/components/LocaleProvider.tsx"));
});

function render(element) {
  return renderToStaticMarkup(createElement(LocaleProvider, null, element));
}

test("landing hero renders the logo, use-case heading and description without the bolt tile", () => {
  const html = render(createElement(LandingHeroBranding, {
    displayName: "Example persona",
    description: "A persona description.",
    productName: "Akte Agent",
  }));

  assert.match(html, /src="\/images\/akte-agent-logo\.png"/);
  assert.match(html, /<h1[^>]*>.*Example persona.*<\/h1>/);
  assert.match(html, /A persona description\./);
  assert.doesNotMatch(html, /animate-glow-pulse|animate-float|fillRule/);
});

test("landing logo is decorative when the heading already names the product", () => {
  const html = render(createElement(LandingHeroBranding, {
    displayName: "Akte Agent",
    description: "A product description.",
    productName: "Akte Agent",
  }));

  assert.match(html, /alt=""/);
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, /<h1[^>]*>.*Akte Agent.*<\/h1>/);
});

test("landing logo has the localized product name when the heading names a persona", () => {
  const html = render(createElement(LandingHeroBranding, {
    displayName: "Example persona",
    description: "A persona description.",
    productName: "Akte Agent",
  }));

  assert.match(html, /alt="Akte Agent"/);
  assert.doesNotMatch(html, /aria-hidden/);
});

test("landing heading falls back to the product name and announces it once", () => {
  const html = render(createElement(LandingHeroBranding, {
    description: "The default description.",
    productName: "Akte Agent",
  }));

  assert.match(html, /alt=""/);
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, /<h1[^>]*>.*Akte Agent.*<\/h1>/);
  assert.match(html, /The default description\./);
});

test("mobile chat header renders an accessible mark and keeps its menu control", () => {
  const html = render(createElement(MobileChatHeader, {
    openSidebar: () => {},
    openSidebarLabel: "Open menu",
  }));

  assert.match(html, /src="\/images\/akte-agent-mark\.png"/);
  assert.match(html, /alt="Akte Agent"/);
  assert.match(html, /<button[^>]*aria-label="Open menu"/);
  assert.match(html, /focus-visible:outline-2/);
  assert.match(html, /shrink-0/);
  assert.ok(html.indexOf("<button") < html.indexOf('src="/images/akte-agent-mark.png"'));
  assert.doesNotMatch(html, /<img[^>]*tabindex=/);
  assert.doesNotMatch(html, />Akte Agent<\/span>/);
});
