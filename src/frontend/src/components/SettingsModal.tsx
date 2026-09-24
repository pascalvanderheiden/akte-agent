"use client";

import { useState, useEffect } from "react";

import { getApiUrl } from "@/lib/config";
import { errorCode, responseError, type ErrorCode } from "@/lib/errors";
import { useLocale } from "./LocaleProvider";

interface AIServiceStatus {
  configured: boolean;
  foundryEndpoint: string;
  foundryModelDeployment: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export function SettingsModal({ open, onClose }: Props) {
  const { t } = useLocale();
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("gpt-52");
  const [status, setStatus] = useState<AIServiceStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorCode | null>(null);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  // Load current settings on open
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSaved(false);
    fetch(`${getApiUrl()}/api/settings`)
      .then(async (res) => {
        if (!res.ok) throw await responseError(res, "SETTINGS_ERROR");
        return res.json();
      })
      .then((data: AIServiceStatus) => {
        if (cancelled) return;
        setStatus(data);
        setEndpoint(data.foundryEndpoint || "");
        setModel(data.foundryModelDeployment || "gpt-52");
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus(null);
        setError(errorCode(err, "SETTINGS_ERROR"));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, loadAttempt]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch(`${getApiUrl()}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          foundryEndpoint: endpoint,
          foundryModelDeployment: model,
        }),
      });
      if (!res.ok) throw await responseError(res, res.status === 405 ? "SETTINGS_READ_ONLY" : "SETTINGS_ERROR");
      const data: AIServiceStatus = await res.json();
      setStatus(data);
      setSaved(true);
    } catch (err) {
      setError(errorCode(err, "SETTINGS_ERROR"));
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-xs z-50 flex items-center justify-center p-4 animate-fade-in" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={t("settings.title")} className="bg-surface rounded-2xl shadow-card max-w-lg w-full border border-border-soft animate-slide-up" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-soft">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-accent from-slate-100 to-slate-50 dark:from-white/8 dark:to-white/4 flex items-center justify-center border border-border-soft">
              <svg className="w-4.5 h-4.5 text-text" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
              </svg>
            </div>
            <div>
              <h2 className="text-base font-semibold text-text">{t("settings.title")}</h2>
              <p className="text-xs text-muted">{t("settings.subtitle")}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label={t("dismiss")}
            className="p-2 text-muted hover:text-text hover:bg-hover rounded-lg transition-all"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-5">
          {loading && <p role="status">{t("loading")}</p>}
          <p className="text-sm text-muted leading-relaxed">{t("settings.description")}</p>

          {/* Status indicator */}
          {status && (
            <div className={`flex items-center gap-2.5 text-sm px-4 py-2.5 rounded-xl ${
              status.configured
                ? "bg-emerald-50 text-emerald-700 border border-emerald-100"
                : "bg-amber-50 text-amber-700 border border-amber-100"
            }`}>
              <span className={`w-2.5 h-2.5 rounded-full ${
                status.configured ? "bg-emerald-500" : "bg-amber-500"
              }`} />
              <span className="font-medium">{status.configured ? t("settings.configured") : t("settings.unconfigured")}</span>
            </div>
          )}

          {/* Foundry Endpoint */}
          <div>
            <label className="block text-sm font-medium text-text mb-1.5">{t("settings.endpoint")}</label>
            <input
              type="url"
              aria-label={t("settings.endpoint")}
              disabled={loading || saving || !status}
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="https://your-resource.services.ai.azure.com"
              className="w-full px-4 py-2.5 bg-surface-2 border border-border-soft rounded-xl text-sm focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all placeholder:text-muted"
            />
          </div>

          {/* Model Deployment */}
          <div>
            <label className="block text-sm font-medium text-text mb-1.5">{t("settings.model")}</label>
            <input
              type="text"
              aria-label={t("settings.model")}
              disabled={loading || saving || !status}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-52"
              className="w-full px-4 py-2.5 bg-surface-2 border border-border-soft rounded-xl text-sm focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all placeholder:text-muted"
            />
          </div>

          {/* Message */}
          {error && <p role="alert" className="text-sm text-red-600">{t(`error.${error}`)}</p>}
          {error && !status && <button onClick={() => setLoadAttempt((n) => n + 1)}>{t("retry")}</button>}
          {saved && <p role="status" className="text-sm text-emerald-600">{t("settings.saved")}</p>}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-border-soft bg-surface-2 rounded-b-2xl">
          <button
            onClick={onClose}
            className="px-5 py-2.5 text-sm text-text bg-surface border border-border-soft rounded-xl hover:bg-hover transition-all font-medium"
          >{t("cancel")}</button>
          <button
            onClick={handleSave}
            disabled={saving || loading || !status}
            className="px-5 py-2.5 text-sm text-accent-fg bg-accent rounded-xl transition-all disabled:opacity-50 font-medium shadow-xs"
          >
            {saving ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
