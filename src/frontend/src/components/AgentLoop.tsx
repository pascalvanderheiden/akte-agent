"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useLocale } from "./LocaleProvider";
import type { TranslationKey } from "@/lib/i18n";

/* ─── Step data ──────────────────────────────────────────────────── */

interface Step {
  id: number;
  label: string;
  title: string;
  source: string;
  description: string;
  detail: string;
  icon: React.ReactNode;
  layer: "you" | "kratos" | "sdk" | "azure";
  isLoop?: boolean;
  visualization: React.ReactNode;
}

/* Layer colors use theme-stable Tailwind hues for the 4-way differentiation
   plus the semantic `accent` token for the Kratos layer so it picks up the
   active theme. Chrome (modal, borders, text) uses semantic tokens. */
const LAYER_COLORS = {
  you: {
    dot:   "bg-cyan-500",
    badge: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20",
    ring:  "ring-cyan-500/30",
    text:  "text-cyan-600 dark:text-cyan-400",
  },
  kratos: {
    dot:   "bg-accent",
    badge: "bg-accent-soft text-accent border-accent/20",
    ring:  "ring-accent/30",
    text:  "text-accent",
  },
  sdk: {
    dot:   "bg-violet-500",
    badge: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
    ring:  "ring-violet-500/30",
    text:  "text-violet-600 dark:text-violet-400",
  },
  azure: {
    dot:   "bg-emerald-500",
    badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
    ring:  "ring-emerald-500/30",
    text:  "text-emerald-600 dark:text-emerald-400",
  },
} as const;



/* Loop step indices (0-indexed): steps 7 (Plan), 8 (Act), 9 (Loop) */
const LOOP_START = 6;
const LOOP_END = 8;

/* ─── Visualizations ─────────────────────────────────────────────── */

function Illustration({ text }: { text: TranslationKey }) {
  const { t } = useLocale();
  return (
    <div className="rounded-xl bg-surface border border-border-soft p-5" role="img" aria-label={t(text)}>
      <p className="text-[10px] uppercase tracking-wider text-muted font-semibold mb-3">{t("help.illustration")}</p>
      <p className="text-sm text-accent font-medium leading-relaxed">{t(text)}</p>
    </div>
  );
}

/* ─── Icons ──────────────────────────────────────────────────────── */

const Icons = {
  sdk: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0l4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0l-5.571 3-5.571-3" />
    </svg>
  ),
  persona: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
    </svg>
  ),
  skills: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.34-3.08a.75.75 0 010-1.298l5.34-3.08a2.25 2.25 0 012.16 0l5.34 3.08a.75.75 0 010 1.298l-5.34 3.08a2.25 2.25 0 01-2.16 0zM4.5 14.25l7.5 4.33 7.5-4.33" />
    </svg>
  ),
  connect: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
    </svg>
  ),
  ask: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.068.157 2.148.279 3.238.364.466.037.893.281 1.153.671L12 21l2.652-3.978c.26-.39.687-.634 1.153-.671 1.09-.086 2.17-.207 3.238-.364 1.584-.233 2.707-1.627 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
    </svg>
  ),
  session: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  plan: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z" />
    </svg>
  ),
  act: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 9.75L16.5 12l-2.25 2.25m-4.5 0L7.5 12l2.25-2.25M6 20.25h12A2.25 2.25 0 0020.25 18V6A2.25 2.25 0 0018 3.75H6A2.25 2.25 0 003.75 6v12A2.25 2.25 0 006 20.25z" />
    </svg>
  ),
  loop: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182M21.012 4.356v4.992" />
    </svg>
  ),
  answer: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
    </svg>
  ),
  audit: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
};

/* ─── Steps ──────────────────────────────────────────────────────── */

function makeSteps(t: ReturnType<typeof useLocale>["t"]): Step[] {
  return [
    { id: 1, layer: "sdk", icon: Icons.sdk, label: t("help.sdk.label"), title: t("help.sdk.title"), source: t("help.sdk.source"), description: t("help.sdk.description"), detail: t("help.sdk.detail"), visualization: <Illustration text="help.sdk.example" /> },
    { id: 2, layer: "kratos", icon: Icons.persona, label: t("help.persona.label"), title: t("help.persona.title"), source: t("help.persona.source"), description: t("help.persona.description"), detail: t("help.persona.detail"), visualization: <Illustration text="help.persona.example" /> },
    { id: 3, layer: "kratos", icon: Icons.skills, label: t("help.skills.label"), title: t("help.skills.title"), source: t("help.skills.source"), description: t("help.skills.description"), detail: t("help.skills.detail"), visualization: <Illustration text="help.skills.example" /> },
    { id: 4, layer: "kratos", icon: Icons.connect, label: t("help.connect.label"), title: t("help.connect.title"), source: t("help.connect.source"), description: t("help.connect.description"), detail: t("help.connect.detail"), visualization: <Illustration text="help.connect.example" /> },
    { id: 5, layer: "you", icon: Icons.ask, label: t("help.ask.label"), title: t("help.ask.title"), source: t("help.ask.source"), description: t("help.ask.description"), detail: t("help.ask.detail"), visualization: <Illustration text="help.ask.example" /> },
    { id: 6, layer: "sdk", icon: Icons.session, label: t("help.session.label"), title: t("help.session.title"), source: t("help.session.source"), description: t("help.session.description"), detail: t("help.session.detail"), visualization: <Illustration text="help.session.example" /> },
    { id: 7, layer: "sdk", icon: Icons.plan, label: t("help.plan.label"), title: t("help.plan.title"), source: t("help.plan.source"), description: t("help.plan.description"), detail: t("help.plan.detail"), visualization: <Illustration text="help.plan.example" />, isLoop: true },
    { id: 8, layer: "kratos", icon: Icons.act, label: t("help.act.label"), title: t("help.act.title"), source: t("help.act.source"), description: t("help.act.description"), detail: t("help.act.detail"), visualization: <Illustration text="help.act.example" />, isLoop: true },
    { id: 9, layer: "sdk", icon: Icons.loop, label: t("help.loop.label"), title: t("help.loop.title"), source: t("help.loop.source"), description: t("help.loop.description"), detail: t("help.loop.detail"), visualization: <Illustration text="help.loop.example" />, isLoop: true },
    { id: 10, layer: "you", icon: Icons.answer, label: t("help.answer.label"), title: t("help.answer.title"), source: t("help.answer.source"), description: t("help.answer.description"), detail: t("help.answer.detail"), visualization: <Illustration text="help.answer.example" /> },
    { id: 11, layer: "azure", icon: Icons.audit, label: t("help.audit.label"), title: t("help.audit.title"), source: t("help.audit.source"), description: t("help.audit.description"), detail: t("help.audit.detail"), visualization: <Illustration text="help.audit.example" /> },
  ];
}

/* ─── Component ──────────────────────────────────────────────────── */

export default function AgentLoop({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, formatNumber } = useLocale();
  const STEPS = makeSteps(t);
  const stepCount = STEPS.length;
  const [activeStep, setActiveStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  const step = STEPS[activeStep];
  const colors = LAYER_COLORS[step.layer];

  const goTo = useCallback((i: number) => {
    setActiveStep(i);
  }, [setActiveStep]);

  const play = useCallback(() => {
    if (isPlaying) {
      if (timerRef.current) clearInterval(timerRef.current);
      setIsPlaying(false);
      return;
    }
    setIsPlaying(true);
    setActiveStep(0);
    let i = 0;
    timerRef.current = setInterval(() => {
      i++;
      if (i >= stepCount) {
        if (timerRef.current) clearInterval(timerRef.current);
        setIsPlaying(false);
        return;
      }
      setActiveStep(i);
    }, 3500);
  }, [isPlaying, stepCount, setActiveStep]);

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  useEffect(() => {
    if (open) cardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeStep, open]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest("select, input, textarea, button")) return;
      if (e.key === "Escape") onClose();
      if (isPlaying) return;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        setActiveStep((s) => Math.min(s + 1, stepCount - 1));
      }
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        setActiveStep((s) => Math.max(s - 1, 0));
      }
      if (e.key === " ") {
        e.preventDefault();
        play();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose, isPlaying, play, stepCount]);

  useEffect(() => {
    if (!open && timerRef.current) {
      clearInterval(timerRef.current);
      setIsPlaying(false);
    }
  }, [open]);

  useEffect(() => {
    if (open) {
      setActiveStep(0);
    }
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-100 flex items-start justify-center overflow-y-auto bg-black/60 backdrop-blur-xs animate-fade-in" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={t("howItWorks")} className="relative w-full max-w-6xl mx-4 my-6 md:my-10 rounded-2xl border border-border bg-surface shadow-card" onClick={(e) => e.stopPropagation()}>
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 w-9 h-9 flex items-center justify-center rounded-xl border border-border bg-surface-2 text-muted hover:text-text-strong hover:bg-hover transition-all"
          aria-label={t("dismiss")}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="px-4 sm:px-6 md:px-10 py-8 md:py-12">
          {/* ── Header ── */}
          <div className="mb-8 md:mb-12 max-w-3xl">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-soft border border-accent/20 mb-4">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-slow" />
              <span className="text-xs font-semibold text-accent tracking-wide uppercase">{t("howItWorks")}</span>
            </div>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-text-strong tracking-tight mb-3">{t("help.title")}</h2>
            <p className="text-text text-base md:text-lg leading-relaxed">{t("help.intro")}</p>
          </div>

          {/* ── Play / step counter ── */}
          <div className="mb-6 flex items-center justify-between">
            <button
              onClick={play}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-surface-2 text-text hover:bg-hover hover:text-text-strong transition-all text-xs font-semibold"
            >
              {isPlaying ? (
                <>
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="5" width="4" height="14" rx="1" />
                    <rect x="14" y="5" width="4" height="14" rx="1" />
                  </svg>{t("help.pause")}</>
              ) : (
                <>
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M5 4.5v15a.5.5 0 00.77.42l12-7.5a.5.5 0 000-.84l-12-7.5A.5.5 0 005 4.5z" />
                  </svg>{t("help.play")}</>
              )}
            </button>
            <div className="text-xs text-muted font-mono">
              {formatNumber(activeStep + 1, { minimumIntegerDigits: 2 })} / {formatNumber(stepCount, { minimumIntegerDigits: 2 })}
            </div>
          </div>

          {/* ── Timeline ── */}
          <div className="relative mb-12 md:mb-16">
            <div className="absolute left-0 right-0 top-6 h-[2px] bg-border rounded-full" />
            <div
              className="absolute left-0 top-6 h-[2px] rounded-full bg-accent transition-all duration-500 ease-out"
              style={{ width: `${(activeStep / (stepCount - 1)) * 100}%` }}
            />

            <div className="relative flex justify-between">
              {STEPS.map((s, i) => {
                const isActive = i === activeStep;
                const isPast = i < activeStep;
                const lc = LAYER_COLORS[s.layer];
                const inLoop = i >= LOOP_START && i <= LOOP_END;
                const isLoopActive = activeStep >= LOOP_START && activeStep <= LOOP_END;

                return (
                  <button
                    key={s.id}
                    onClick={() => !isPlaying && goTo(i)}
                    disabled={isPlaying}
                    className="flex flex-col items-center group outline-hidden focus-visible:ring-2 focus-visible:ring-accent rounded-xl"
                    aria-label={t("help.step", { count: s.id, name: s.title })}
                    aria-current={isActive ? "step" : undefined}
                  >
                    <div
                      className={`
                        relative w-12 h-12 rounded-xl flex items-center justify-center
                        transition-all duration-300 border
                        ${isActive
                          ? `bg-surface-2 border-border shadow-card ring-2 ${lc.ring} scale-105`
                          : isPast
                          ? `bg-surface-2 border-border ${lc.text}`
                          : "bg-surface border-border-soft text-muted group-hover:border-border group-hover:shadow-card"
                        }
                      `}
                    >
                      <span className={isActive ? lc.text : ""}>
                        {isActive ? s.icon : (
                          <span className="text-xs font-bold">{formatNumber(s.id)}</span>
                        )}
                      </span>
                    </div>
                    <span
                      className={`
                        mt-1.5 text-[11px] font-medium transition-colors whitespace-nowrap
                        ${isActive ? lc.text : "text-muted group-hover:text-text"}
                      `}
                    >
                      {s.label}
                    </span>
                    {inLoop && (
                      <div className="flex flex-col items-center mt-1.5">
                        <div className={`w-[2px] h-2 rounded-full transition-colors duration-300 ${
                          isLoopActive ? "bg-accent" : "bg-border"
                        }`} />
                      </div>
                    )}
                  </button>
                );
              })}
            </div>

            {/* Loop bracket */}
            {(() => {
              const total = stepCount;
              const leftPct = (LOOP_START / (total - 1)) * 100;
              const rightPct = (LOOP_END / (total - 1)) * 100;
              const widthPct = rightPct - leftPct;
              const isLoopActive = activeStep >= LOOP_START && activeStep <= LOOP_END;
              return (
                <div
                  className="absolute hidden md:block pointer-events-none"
                  style={{ left: `${leftPct}%`, width: `${widthPct}%`, top: "calc(100% - 6px)" }}
                >
                  <svg className="w-full" viewBox="0 0 200 28" preserveAspectRatio="none" fill="none" aria-hidden>
                    <path
                      d="M 4 0 L 4 14 Q 4 22 12 22 L 188 22 Q 196 22 196 14 L 196 0"
                      stroke="currentColor"
                      className={`transition-colors duration-300 ${isLoopActive ? "text-accent" : "text-border"}`}
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      fill="none"
                    />
                    <path
                      d="M 1 6 L 4 0 L 7 6"
                      stroke="currentColor"
                      className={`transition-colors duration-300 ${isLoopActive ? "text-accent" : "text-border"}`}
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      fill="none"
                    />
                  </svg>
                  <div className="flex items-center justify-center gap-1.5 -mt-0.5">
                    <svg className={`w-3 h-3 transition-colors duration-300 ${isLoopActive ? "text-accent" : "text-muted"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182M21.012 4.356v4.992" />
                    </svg>
                    <span className={`text-[10px] font-bold tracking-wider uppercase transition-colors duration-300 ${isLoopActive ? "text-accent" : "text-muted"}`}>{t("help.loop.title")}</span>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* ── Detail card ── */}
          <div
            ref={cardRef}
            className="rounded-2xl border border-border bg-surface-2 shadow-card overflow-hidden animate-fade-in"
            key={activeStep}
          >
            <div className="h-1 bg-accent" />
            <div className="p-6 md:p-8">
              <div className="flex flex-wrap items-start gap-3 mb-4">
                <span className={`inline-flex items-center justify-center w-9 h-9 rounded-xl border text-sm font-bold ${colors.badge}`}>
                  {formatNumber(step.id)}
                </span>
                <div className="flex-1 min-w-0">
                  <h3 className="text-xl md:text-2xl font-bold text-text-strong tracking-tight">
                    {step.title}
                  </h3>
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${colors.text}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${colors.dot}`} />
                      {step.layer === "you" ? t("help.you") : step.layer === "kratos" ? "Akte Agent" : step.layer === "sdk" ? "Copilot SDK" : "Azure"}
                    </span>
                    <span className="text-border">·</span>
                    <span className="text-[11px] text-muted truncate">{step.source}</span>
                  </div>
                </div>
                {step.isLoop && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-accent-soft text-accent border border-accent/20">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182M21.012 4.356v4.992" />
                    </svg>{t("help.inLoop")}</span>
                )}
              </div>

              <p className="text-text text-sm md:text-[15px] leading-relaxed mb-5">
                {step.description}
              </p>

              <div className="mb-5">
                {step.visualization}
              </div>

              <p className="text-text-strong text-[14px] md:text-[15px] leading-relaxed">
                {step.detail}
              </p>
            </div>
          </div>

          {/* ── Nav arrows ── */}
          <div className="mt-6 flex items-center justify-between">
            <button
              onClick={() => setActiveStep((s) => Math.max(s - 1, 0))}
              disabled={activeStep === 0 || isPlaying}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-text hover:bg-hover hover:text-text-strong disabled:opacity-30 disabled:cursor-not-allowed transition-all text-xs font-semibold"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>{t("help.previous")}</button>
            <span className="text-[11px] text-muted hidden md:inline">{t("help.tip")}</span>
            <button
              onClick={() => setActiveStep((s) => Math.min(s + 1, stepCount - 1))}
              disabled={activeStep === stepCount - 1 || isPlaying}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-text hover:bg-hover hover:text-text-strong disabled:opacity-30 disabled:cursor-not-allowed transition-all text-xs font-semibold"
            >{t("help.next")}<svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
