"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Locale } from "@/types";
import { isLocale, LOCALE_STORAGE_KEY, localeTag, resolveLocale, translate, type TranslationKey, type TranslationParams } from "@/lib/i18n";

function createLocaleValue(locale: Locale, setLocale: (locale: Locale) => void, ready: boolean) {
  const tag = localeTag[locale];
  return {
    locale,
    setLocale,
    ready,
    t: (key: TranslationKey, params?: TranslationParams) => translate(locale, key, params),
    formatNumber: (value: number, options?: Intl.NumberFormatOptions) => new Intl.NumberFormat(tag, options).format(value),
    formatDate: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(tag, options).format(new Date(value)),
    formatRelativeTime: (value: number, unit: Intl.RelativeTimeFormatUnit) => new Intl.RelativeTimeFormat(tag, { numeric: "auto" }).format(value, unit),
    formatDuration: (ms: number) => new Intl.NumberFormat(tag, { maximumFractionDigits: 1, style: "unit", unit: ms < 1000 ? "millisecond" : "second", unitDisplay: "short" }).format(ms < 1000 ? ms : ms / 1000),
  };
}

const LocaleContext = createContext<ReturnType<typeof createLocaleValue> | null>(null);

export function useLocale() {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale requires LocaleProvider");
  return value;
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  // Stable server/first-client render for static export; resolve only after hydration.
  const [locale, setLocaleState] = useState<Locale>("en");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    } catch {
      // Storage is optional in embedded/private contexts.
    }
    setLocaleState(resolveLocale(saved, navigator.languages?.length ? navigator.languages : [navigator.language]));
    setReady(true);
  }, []);

  const setLocale = useCallback((value: Locale) => {
    setLocaleState(value);
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, value);
    } catch {
      // Keep the explicit choice for this visit even without storage.
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.querySelector('meta[name="description"]')?.setAttribute("content", translate(locale, "app.description"));
  }, [locale]);

  const value = useMemo(() => createLocaleValue(locale, setLocale, ready), [locale, setLocale, ready]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function LanguageSelector() {
  const { locale, setLocale, t } = useLocale();
  return (
    <label data-locale-selector className="fixed top-2 right-3 z-[250] text-sm text-text">
      <span className="sr-only">{t("language")}</span>
      <select
        className="rounded border border-border bg-surface px-2 py-1"
        value={locale}
        onChange={(event) => { if (isLocale(event.target.value)) setLocale(event.target.value); }}
      >
        <option value="en">English</option>
        <option value="nl">Nederlands</option>
      </select>
    </label>
  );
}
