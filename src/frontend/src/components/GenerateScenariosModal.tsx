"use client";

import { useState, type JSX } from "react";
import { generateEvalScenarios, upsertEvalScenario } from "@/lib/api";
import { useLocale } from "./LocaleProvider";
import { errorCode, type ErrorCode } from "@/lib/errors";
import type { EvalScenario } from "@/types";

interface Props {
  useCase: string;
  personaLabel: string;
  open: boolean;
  onClose: () => void;
  onGenerated: (scenarios: EvalScenario[]) => void;
}

export function GenerateScenariosModal({ useCase, personaLabel, open, onClose, onGenerated }: Props): JSX.Element | null {
  const { t, formatNumber } = useLocale();
  const [count, setCount] = useState(10);
  const [instructions, setInstructions] = useState("");
  const [drafts, setDrafts] = useState<EvalScenario[]>([]);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorCode | "">("");
  const [generated, setGenerated] = useState(false);
  const validCount = Number.isInteger(count) && count >= 1 && count <= 50;
  const validDrafts = drafts.every((d) => d.name.trim() && d.input_message.trim() && d.expected_behavior.trim());

  if (!open) return null;

  const handleGenerate = async () => {
    if (!validCount || generating || saving) return;
    setGenerating(true);
    setError("");
    setDrafts([]);
    setGenerated(false);
    try {
      const result = await generateEvalScenarios(useCase, { count, instructions: instructions || undefined, persist: false });
      setDrafts(result.scenarios);
      setGenerated(true);
    } catch (err) {
      setError(errorCode(err, "EVAL_GENERATE"));
    } finally {
      setGenerating(false);
    }
  };

  const updateDraft = (idx: number, patch: Partial<EvalScenario>) => {
    setDrafts((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  };

  const removeDraft = (idx: number) => {
    setDrafts((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSaveAll = async () => {
    if (drafts.length === 0 || !validDrafts || saving || generating) return;
    setSaving(true);
    setError("");
    try {
      for (const scenario of drafts) {
        const s = await upsertEvalScenario(useCase, scenario);
        setDrafts((remaining) => remaining.filter((d) => d !== scenario));
        onGenerated([s]);
      }
      onClose();
      setDrafts([]);
      setInstructions("");
      setGenerated(false);
    } catch (err) {
      setError(errorCode(err, "EVAL_SAVE"));
    } finally {
      setSaving(false);
    }
  };

  const handleClose = () => {
    if (!generating && !saving) {
      setDrafts([]);
      setInstructions("");
      setError("");
      setGenerated(false);
      onClose();
    }
  };

  return (
    <div
      role="dialog" aria-modal="true" aria-label={t("eval.generate")}
      className="fixed inset-0 bg-black/40 backdrop-blur-xs z-50 flex items-center justify-center p-4 animate-fade-in"
      onClick={handleClose}
    >
      <div
        className="bg-surface rounded-2xl shadow-card max-w-2xl w-full border border-border-soft animate-slide-up flex flex-col max-h-[90vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-soft shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-accent flex items-center justify-center border border-accent">
              <svg className="w-4 h-4 text-accent-fg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
              </svg>
            </div>
            <div>
              <h2 className="text-base font-semibold text-text">{t("eval.generate")}</h2>
              <p className="text-xs text-muted">{t("eval.generatedFor", { persona: personaLabel })}</p>
            </div>
          </div>
          <button
            onClick={handleClose}
            aria-label={t("eval.cancel")}
            disabled={generating || saving}
            className="p-2 text-muted hover:text-text hover:bg-hover rounded-lg transition-all disabled:opacity-40"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {error && (
            <div role="alert" className="px-4 py-3 bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 text-sm rounded-xl border border-red-100 dark:border-red-500/20 flex items-center justify-between">
              <span>{t(`error.${error}`)} ({error})</span>
              <button onClick={() => setError("")} aria-label={t("dismiss")} className="text-red-400 hover:text-red-600 transition-colors ml-3">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}
          {!validCount && <p role="status">{t("eval.countValidation")}</p>}
          {!validDrafts && <p role="status">{t("eval.validation")}</p>}
          {generated && drafts.length === 0 && !saving && <p role="status">{t("eval.noDrafts")}</p>}
          <fieldset disabled={generating || saving} className="space-y-5">

          {/* Generation params */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text mb-1.5">
                {t("eval.count")}
              </label>
              <input
                type="number"
                min={1}
                max={50}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                aria-label={t("eval.count")}
                aria-invalid={!validCount}
                className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-text mb-1.5">
                {t("eval.instructions")} <span className="text-muted font-normal">{t("eval.optional")}</span>
              </label>
              <textarea
                value={instructions}
                aria-label={t("eval.instructions")}
                onChange={(e) => setInstructions(e.target.value)}
                rows={2}
                placeholder={t("eval.instructionsPlaceholder")}
                className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all resize-none"
              />
            </div>
          </div>

          <div>
            <button
              onClick={handleGenerate}
              disabled={generating || saving || !validCount}
              className="flex items-center gap-2 px-5 py-2.5 text-sm text-accent-fg bg-accent rounded-xl transition-all shadow-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {generating ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white/30 border-t-white" />
                  {t("eval.generating")}
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                  </svg>
                  {t("eval.generateDraft")}
                </>
              )}
            </button>
          </div>

          {/* Draft list */}
          {drafts.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-text">
                  {t("eval.drafts")} <span className="font-mono text-xs text-muted">({formatNumber(drafts.length)})</span>
                </h3>
                <p className="text-xs text-muted">{t("eval.reviewHint")}</p>
              </div>
              {drafts.map((scenario, idx) => (
                <div
                  key={idx}
                  className="border border-border-soft rounded-xl p-4 space-y-3 bg-surface-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <input
                      type="text"
                      value={scenario.name}
                      aria-label={t("eval.scenarioName")}
                      onChange={(e) => updateDraft(idx, { name: e.target.value })}
                      className="flex-1 px-2.5 py-1.5 bg-surface border border-border-soft rounded-lg text-sm font-medium text-text focus:outline-hidden focus:ring-1 focus:ring-accent"
                      placeholder={t("eval.scenarioName")}
                    />
                    <button
                      onClick={() => removeDraft(idx)}
                      className="p-1.5 text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-all"
                      title={t("eval.remove")}
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  <textarea
                    value={scenario.input_message}
                    aria-label={t("eval.inputMessage")}
                    onChange={(e) => updateDraft(idx, { input_message: e.target.value })}
                    rows={2}
                    placeholder={t("eval.inputPlaceholder")}
                    className="w-full px-2.5 py-1.5 bg-surface border border-border-soft rounded-lg text-sm text-text focus:outline-hidden focus:ring-1 focus:ring-accent resize-none"
                  />
                  <textarea
                    value={scenario.expected_behavior}
                    aria-label={t("eval.expectedBehavior")}
                    onChange={(e) => updateDraft(idx, { expected_behavior: e.target.value })}
                    rows={2}
                    placeholder={t("eval.expectedPlaceholder")}
                    className="w-full px-2.5 py-1.5 bg-surface border border-border-soft rounded-lg text-sm text-text focus:outline-hidden focus:ring-1 focus:ring-accent resize-none"
                  />
                </div>
              ))}
            </div>
          )}
          </fieldset>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border-soft shrink-0">
          <button
            onClick={handleClose}
            disabled={generating || saving}
            className="px-4 py-2 text-sm text-muted bg-surface-2 border border-border-soft rounded-xl hover:bg-hover transition-all font-medium disabled:opacity-40"
          >
            {t("eval.cancel")}
          </button>
          {drafts.length > 0 && (
            <button
              onClick={handleSaveAll}
              disabled={saving || generating || !validDrafts}
              className="flex items-center gap-2 px-5 py-2 text-sm text-accent-fg bg-accent rounded-xl transition-all shadow-xs font-medium disabled:opacity-50"
            >
              {saving ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white/30 border-t-white" />
                  {t("eval.saving")}
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  {t("eval.saveAll", { count: formatNumber(drafts.length) })}
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
