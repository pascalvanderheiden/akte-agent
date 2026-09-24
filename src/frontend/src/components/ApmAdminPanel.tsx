"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getApmStatus, installApmMcpServer, installApmPackage, syncApm, uninstallApmMcpServer, uninstallApmPackage, updateApm } from "@/lib/api";
import type { ApmCommandResponse, ApmDependency, ApmMcpServer } from "@/types";
import type { TranslationKey } from "@/lib/i18n";
import { useLocale } from "./LocaleProvider";
import { ApplicationError, errorCode, type ErrorCode } from "@/lib/errors";

interface Props {
  useCase: string;
  onMcpChange?: () => void;
}

interface SuggestedPackage {
  id: string;
  name: string;
  pkg: string;
  ref?: string;
  description: TranslationKey;
  homepage: string;
}

// Curated list of real, publicly available APM packages. These are documented
// in the repo README and verified to resolve through the apm CLI.
const SUGGESTED_PACKAGES: SuggestedPackage[] = [
  {
    id: "microsoft/apm-sample-package",
    name: "APM sample package",
    pkg: "microsoft/apm-sample-package",
    ref: "v1.0.0",
    description: "apm.suggestion1",
    homepage: "https://github.com/microsoft/apm-sample-package",
  },
  {
    id: "anthropics/skills/skills/frontend-design",
    name: "Frontend design (Anthropic Skills)",
    pkg: "anthropics/skills/skills/frontend-design",
    description: "apm.suggestion2",
    homepage: "https://github.com/anthropics/skills/tree/main/skills/frontend-design",
  },
  {
    id: "github/awesome-copilot/plugins/context-engineering",
    name: "Context engineering (awesome-copilot)",
    pkg: "github/awesome-copilot/plugins/context-engineering",
    description: "apm.suggestion3",
    homepage: "https://github.com/github/awesome-copilot",
  },
  {
    id: "microsoft/GitHub-Copilot-for-Azure/plugin/skills/azure-compliance",
    name: "Azure compliance skill",
    pkg: "microsoft/GitHub-Copilot-for-Azure/plugin/skills/azure-compliance",
    description: "apm.suggestion4",
    homepage: "https://github.com/microsoft/GitHub-Copilot-for-Azure",
  },
];

interface SuggestedMcpServer {
  id: string;
  name: string;
  transport: "stdio" | "http" | "sse";
  command?: string;
  args?: string[];
  url?: string;
  description: TranslationKey;
  homepage: string;
}

// Curated MCP servers known to work out-of-the-box via `apm mcp install`.
// All are published in the APM MCP registry (see `apm mcp list`).
const SUGGESTED_MCP_SERVERS: SuggestedMcpServer[] = [
  {
    id: "markitdown",
    name: "markitdown",
    transport: "stdio",
    command: "uvx",
    args: ["markitdown-mcp"],
    description: "apm.suggestion5",
    homepage: "https://github.com/microsoft/markitdown",
  },
  {
    id: "playwright",
    name: "playwright",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@playwright/mcp@latest"],
    description: "apm.suggestion6",
    homepage: "https://github.com/microsoft/playwright-mcp",
  },
  {
    id: "context7",
    name: "context7",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@upstash/context7-mcp@latest"],
    description: "apm.suggestion7",
    homepage: "https://github.com/upstash/context7",
  },
  {
    id: "github",
    name: "github",
    transport: "http",
    url: "https://api.githubcopilot.com/mcp/",
    description: "apm.suggestion8",
    homepage: "https://github.com/github/github-mcp-server",
  },
  {
    id: "microsoft-learn",
    name: "microsoft-learn",
    transport: "http",
    url: "https://learn.microsoft.com/api/mcp",
    description: "apm.suggestion9",
    homepage: "https://github.com/MicrosoftDocs/mcp",
  },
];

export function ApmAdminPanel({ useCase, onMcpChange }: Props) {
  const { t, formatDuration, formatNumber } = useLocale();
  const [dependencies, setDependencies] = useState<ApmDependency[]>([]);
  const [mcpServers, setMcpServers] = useState<ApmMcpServer[]>([]);
  const [version, setVersion] = useState<string>("");
  const [materialisedDirs, setMaterialisedDirs] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ErrorCode | null>(null);
  const [disabled, setDisabled] = useState(false); // 503 → APM disabled
  const [busy, setBusy] = useState<string | null>(null); // tag of in-flight op

  // Install form
  const [pkgInput, setPkgInput] = useState("");
  const [refInput, setRefInput] = useState("");
  const [devInput, setDevInput] = useState(false);

  // MCP install form
  const [mcpName, setMcpName] = useState("");
  const [mcpTransport, setMcpTransport] = useState<"stdio" | "http" | "sse">("stdio");
  const [mcpCommand, setMcpCommand] = useState("");
  const [mcpArgsInput, setMcpArgsInput] = useState("");
  const [mcpUrl, setMcpUrl] = useState("");
  const [mcpEnvInput, setMcpEnvInput] = useState("");

  // Last command output
  const [lastResult, setLastResult] = useState<ApmCommandResponse | null>(null);
  const [outputOpen, setOutputOpen] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getApmStatus(useCase);
      setDependencies(data.dependencies);
      setMcpServers(data.mcp_servers ?? []);
      setVersion(data.version);
      setMaterialisedDirs(data.materialised_skill_dirs);
      setDisabled(false);
    } catch (err) {
      if (err instanceof ApplicationError && err.status === 503) {
        setDisabled(true);
      } else {
        setError(errorCode(err, "APM_ERROR"));
      }
    } finally {
      setLoading(false);
    }
  }, [useCase]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const applyResult = (result: ApmCommandResponse) => {
    setLastResult(result);
    setOutputOpen(true);
    setDependencies(result.dependencies);
  };

  const runOp = async (tag: string, op: () => Promise<ApmCommandResponse>) => {
    setBusy(tag);
    setLastResult(null);
    setError(null);
    try {
      const result = await op();
      if (!result.success) {
        setLastResult(result);
        setOutputOpen(false);
        setError("APM_ERROR");
        return false;
      }
      applyResult(result);
      // Refresh full status (version + materialised dirs may change)
      await loadStatus();
      return true;
    } catch (err) {
      setError(errorCode(err, "APM_ERROR"));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const handleInstall = async (e: React.FormEvent) => {
    e.preventDefault();
    const pkg = pkgInput.trim();
    if (!pkg) { setError("INVALID_REQUEST"); return; }
    const succeeded = await runOp("install", () =>
      installApmPackage(useCase, {
        package: pkg,
        ref: refInput.trim() || undefined,
        dev: devInput || undefined,
      })
    );
    if (!succeeded) return;
    setPkgInput("");
    setRefInput("");
    setDevInput(false);
  };

  const handleUninstall = async (pkg: string) => {
    if (!confirm(t("apm.uninstallConfirm", { name: pkg }))) return;
    await runOp(`uninstall:${pkg}`, () => uninstallApmPackage(useCase, pkg));
  };

  const handleSync = () => runOp("sync", () => syncApm(useCase));
  const handleUpdateAll = () => runOp("update-all", () => updateApm(useCase, {}));

  const installedIds = useMemo(
    () => new Set(dependencies.map((d) => d.name)),
    [dependencies]
  );

  const handleInstallSuggested = async (pkg: SuggestedPackage) => {
    await runOp(`install-suggested:${pkg.id}`, () =>
      installApmPackage(useCase, {
        package: pkg.pkg,
        ref: pkg.ref,
      })
    );
  };

  const installedMcpNames = useMemo(
    () => new Set(mcpServers.map((s) => s.name)),
    [mcpServers]
  );

  const parseEnvLines = (raw: string): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq <= 0) continue;
      out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
    }
    return out;
  };

  const handleInstallMcp = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = mcpName.trim();
    if (!name) { setError("MCP_NAME_REQUIRED"); return; }
    if (mcpTransport === "stdio" && !mcpCommand.trim()) { setError("MCP_COMMAND_REQUIRED"); return; }
    if (mcpTransport !== "stdio") {
      try {
        if (!["http:", "https:"].includes(new URL(mcpUrl).protocol)) throw new Error("protocol");
      } catch {
        setError("MCP_URL_REQUIRED");
        return;
      }
    }
    if (mcpEnvInput.split("\n").some((line) => line.trim() && !line.trim().startsWith("#") && line.indexOf("=") <= 0)) {
      setError("MCP_LINES_INVALID");
      return;
    }
    const args = mcpArgsInput
      .split(/\s+/)
      .map((a) => a.trim())
      .filter(Boolean);
    const env = parseEnvLines(mcpEnvInput);
    const succeeded = await runOp(`install-mcp:${name}`, () =>
      installApmMcpServer(useCase, {
        name,
        transport: mcpTransport,
        command: mcpTransport === "stdio" ? mcpCommand.trim() || undefined : undefined,
        args: mcpTransport === "stdio" && args.length ? args : undefined,
        url: mcpTransport !== "stdio" ? mcpUrl.trim() || undefined : undefined,
        env: Object.keys(env).length ? env : undefined,
      })
    );
    if (!succeeded) return;
    onMcpChange?.();
    setMcpName("");
    setMcpCommand("");
    setMcpArgsInput("");
    setMcpUrl("");
    setMcpEnvInput("");
  };

  const handleInstallSuggestedMcp = async (s: SuggestedMcpServer) => {
    const succeeded = await runOp(`install-suggested-mcp:${s.id}`, () =>
      installApmMcpServer(useCase, {
        name: s.name,
        transport: s.transport,
        command: s.command,
        args: s.args,
        url: s.url,
      })
    );
    if (succeeded) onMcpChange?.();
  };

  const handleUninstallMcp = async (name: string) => {
    if (!confirm(t("apm.mcpConfirm", { name }))) return;
    const succeeded = await runOp(`uninstall-mcp:${name}`, () => uninstallApmMcpServer(useCase, name));
    if (succeeded) onMcpChange?.();
  };

  if (disabled) {
    return (
      <div className="max-w-2xl">
        <div className="px-6 py-8 bg-surface-2 border border-border-soft rounded-2xl text-center">
          <div className="mx-auto mb-3 w-10 h-10 rounded-xl bg-surface-2 flex items-center justify-center">
            <svg className="w-5 h-5 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-text">{t("apm.disabled")}</h3>
          <p className="mt-1 text-xs text-muted">
            {t("apm.disabledHelp")}
          </p>
        </div>
      </div>
    );
  }

  const anyBusy = busy !== null || loading;
  const spin = (
    <span className="inline-block align-middle w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
  );

  return (
    <div className="space-y-6">
      {/* Header block */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h3 className="text-base font-semibold text-text">{t("apm.title")}</h3>
          <p className="text-xs text-muted mt-0.5 font-mono">
            apm CLI: {version || "…"}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleSync}
            disabled={anyBusy}
            className="flex items-center gap-2 px-4 py-2 text-sm text-text bg-surface-2 border border-border-soft rounded-xl hover:bg-hover transition-all font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {busy === "sync" ? spin : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
            )}
            {t("apm.sync")}
          </button>
          <button
            onClick={handleUpdateAll}
            disabled={anyBusy}
            className="flex items-center gap-2 px-4 py-2 text-sm text-accent-fg bg-accent rounded-xl transition-all shadow-xs font-medium disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {busy === "update-all" ? spin : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12a7.5 7.5 0 0 0 13 5.196M19.5 12a7.5 7.5 0 0 0-13-5.196M15 5h4.5V.5M9 19H4.5V23.5" />
              </svg>
            )}
            {t("apm.update")}
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div role="alert" className="px-4 py-3 bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 text-sm rounded-xl border border-red-100 dark:border-red-500/20 flex items-start justify-between gap-3">
          <p className="text-xs flex-1">{t(`error.${error}`)}</p>
          <button aria-label={t("dismiss")} onClick={() => setError(null)} className="text-red-400 hover:text-red-600 transition-colors shrink-0">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {busy && <p role="status">{t("auth.working")}</p>}
      {lastResult?.success && <p role="status">{t("apm.success")}</p>}

      {/* Dependency table */}
      <div className="bg-surface border border-border-soft rounded-2xl overflow-hidden">
        {loading ? (
          <div role="status" aria-label={t("loading")} className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-2 border-accent border-t-transparent" />
          </div>
        ) : dependencies.length === 0 && !error ? (
          <div className="px-6 py-10 text-center text-sm text-muted">{t("apm.empty")}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("skills.name")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.ref")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.resolved")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.source")}</th>
                  <th className="px-4 py-2.5 text-right font-semibold">{t("apm.actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200/80 dark:divide-white/6">
                {dependencies.map((dep) => {
                  const tag = `uninstall:${dep.name}`;
                  return (
                    <tr key={dep.name} className="hover:bg-hover transition-colors">
                      <td className="px-4 py-2.5 font-mono text-text">{dep.name}</td>
                      <td className="px-4 py-2.5 font-mono text-text">{dep.ref ?? "—"}</td>
                      <td className="px-4 py-2.5 font-mono text-text truncate max-w-[220px]" title={dep.resolved ?? ""}>
                        {dep.resolved ?? "—"}
                      </td>
                      <td className="px-4 py-2.5 text-text">{dep.source}</td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => handleUninstall(dep.name)}
                          disabled={anyBusy}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-lg hover:bg-red-100 dark:hover:bg-red-500/20 transition-all font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {busy === tag ? spin : (
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                            </svg>
                          )}
                          {t("delete")}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* MCP servers provided by APM */}
      <div className="bg-surface border border-border-soft rounded-2xl p-5">
        <div className="mb-4">
          <h4 className="text-sm font-semibold text-text">{t("apm.mcpTitle")}</h4>
          <p className="text-xs text-muted mt-0.5">
            {t("apm.mcpHelp")}
          </p>
        </div>
        {mcpServers.length === 0 && !error ? (
          <div className="px-4 py-6 text-center text-sm text-muted">{t("apm.noMcp")}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("skills.name")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.transport")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.commandUrl")}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t("apm.state")}</th>
                  <th className="px-4 py-2.5 text-right font-semibold">{t("apm.actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200/80 dark:divide-white/6">
                {mcpServers.map((s) => {
                  const isLocal = s.transport === "stdio";
                  const commandOrUrl = isLocal
                    ? s.command
                      ? `${s.command}${s.args && s.args.length ? " " + s.args.join(" ") : ""}`
                      : ""
                    : s.url ?? "";
                  const pending = !s.command && !s.url;
                  const tag = `uninstall-mcp:${s.name}`;
                  return (
                    <tr key={s.name} className="hover:bg-hover transition-colors">
                      <td className="px-4 py-2.5 font-mono text-text">{s.name}</td>
                      <td className="px-4 py-2.5">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium ${
                          isLocal
                            ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            : "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400"
                        }`}>{s.transport}</span>
                      </td>
                      <td className="px-4 py-2.5 font-mono text-text truncate max-w-[280px]" title={commandOrUrl}>
                        {commandOrUrl || "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        {pending ? (
                          <span className="text-[11px] text-amber-600 dark:text-amber-400">{t("apm.pending")}</span>
                        ) : (
                          <span className="text-[11px] text-emerald-600 dark:text-emerald-400">{t("apm.active")}</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => handleUninstallMcp(s.name)}
                          disabled={anyBusy}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-lg hover:bg-red-100 dark:hover:bg-red-500/20 transition-all font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {busy === tag ? spin : t("delete")}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Suggested MCP servers */}
      <div className="bg-surface border border-border-soft rounded-2xl p-5">
        <div className="mb-4">
          <h4 className="text-sm font-semibold text-text">{t("apm.suggestedMcp")}</h4>
          <p className="text-xs text-muted mt-0.5">
            {t("apm.suggestedMcpHelp")}
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {SUGGESTED_MCP_SERVERS.map((s) => {
            const installed = installedMcpNames.has(s.name);
            const tag = `install-suggested-mcp:${s.id}`;
            const runtimeHint = s.transport === "stdio"
              ? `${s.command}${s.args && s.args.length ? " " + s.args.join(" ") : ""}`
              : s.url ?? "";
            return (
              <div
                key={s.id}
                className="flex flex-col gap-2 p-4 bg-surface-2 border border-border-soft rounded-xl"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-text truncate">{s.name}</span>
                      <span className={`text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded-md ${
                        s.transport === "stdio"
                          ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                          : "bg-blue-500/15 text-blue-600 dark:text-blue-400"
                      }`}>{s.transport}</span>
                    </div>
                    <a
                      href={s.homepage}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[11px] font-mono text-accent hover:underline break-all"
                      title={runtimeHint}
                    >
                      {runtimeHint}
                    </a>
                  </div>
                  {installed && (
                    <span className="shrink-0 text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">{t("apm.installed")}</span>
                  )}
                </div>
                <p className="text-xs text-text leading-relaxed">
                  {t(s.description)}
                </p>
                <div className="flex justify-end mt-1">
                  <button
                    onClick={() => handleInstallSuggestedMcp(s)}
                    disabled={anyBusy || installed}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-accent-fg bg-accent rounded-lg transition-all shadow-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {busy === tag ? spin : (
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                      </svg>
                    )}
                    {installed ? t("apm.installed") : t("apm.install")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Custom MCP install form */}
      <form noValidate onSubmit={handleInstallMcp} className="bg-surface border border-border-soft rounded-2xl p-5 space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-text">{t("apm.installMcp")}</h4>
          <p className="text-xs text-muted mt-0.5">
            {t("apm.installMcpHelp")}
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium text-text mb-1.5">{t("skills.name")}<span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              aria-label={t("mcp.name")} value={mcpName}
              onChange={(e) => setMcpName(e.target.value)}
              placeholder="markitdown"
              required
              className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-text mb-1.5">{t("apm.transport")}</label>
            <select
              aria-label={t("apm.transport")} value={mcpTransport}
              onChange={(e) => setMcpTransport(e.target.value as "stdio" | "http" | "sse")}
              className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
            >
              <option value="stdio">stdio</option>
              <option value="http">http</option>
              <option value="sse">sse</option>
            </select>
          </div>
          {mcpTransport === "stdio" ? (
            <div>
              <label className="block text-xs font-medium text-text mb-1.5">{t("mcp.command")}<span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                aria-label={t("mcp.command")} value={mcpCommand}
                onChange={(e) => setMcpCommand(e.target.value)}
                placeholder="uvx"
                required
                className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
              />
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium text-text mb-1.5">
                URL <span className="text-red-500">*</span>
              </label>
              <input
                type="url"
                aria-label={t("apm.commandUrl")} value={mcpUrl}
                onChange={(e) => setMcpUrl(e.target.value)}
                placeholder="https://example.com/mcp"
                required
                className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
              />
            </div>
          )}
        </div>
        {mcpTransport === "stdio" && (
          <div>
            <label className="block text-xs font-medium text-text mb-1.5">{t("apm.args")}<span className="text-muted font-normal">{t("apm.spaces")}</span>
            </label>
            <input
              type="text"
              aria-label={t("apm.args")} value={mcpArgsInput}
              onChange={(e) => setMcpArgsInput(e.target.value)}
              placeholder="markitdown-mcp"
              className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
            />
          </div>
        )}
        <div>
          <label className="block text-xs font-medium text-text mb-1.5">{t("apm.env")}<span className="text-muted font-normal">{t("apm.envHint")}</span>
          </label>
          <textarea
            aria-label={t("mcp.env")} value={mcpEnvInput}
            onChange={(e) => setMcpEnvInput(e.target.value)}
            placeholder="GITHUB_TOKEN=ghp_...&#10;DEBUG=true"
            rows={2}
            className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
          />
        </div>
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={anyBusy || !mcpName.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm text-accent-fg bg-accent rounded-xl transition-all shadow-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {busy === `install-mcp:${mcpName.trim()}` ? spin : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            )}
            {t("apm.installMcp")}
          </button>
        </div>
      </form>

      {/* Suggested packages */}
      <div className="bg-surface border border-border-soft rounded-2xl p-5">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h4 className="text-sm font-semibold text-text">{t("apm.suggested")}</h4>
            <p className="text-xs text-muted mt-0.5">
              {t("apm.suggestedHelp", { name: useCase })}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {SUGGESTED_PACKAGES.map((pkg) => {
            const installed = installedIds.has(pkg.pkg);
            const tag = `install-suggested:${pkg.id}`;
            return (
              <div
                key={pkg.id}
                className="flex flex-col gap-2 p-4 bg-surface-2 border border-border-soft rounded-xl"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-text truncate">
                      {pkg.name}
                    </div>
                    <a
                      href={pkg.homepage}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[11px] font-mono text-accent hover:underline break-all"
                    >
                      {pkg.pkg}
                      {pkg.ref ? `#${pkg.ref}` : ""}
                    </a>
                  </div>
                  {installed && (
                    <span className="shrink-0 text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">{t("apm.installed")}</span>
                  )}
                </div>
                <p className="text-xs text-text leading-relaxed">
                  {t(pkg.description)}
                </p>
                <div className="flex justify-end mt-1">
                  <button
                    onClick={() => handleInstallSuggested(pkg)}
                    disabled={anyBusy || installed}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-accent-fg bg-accent rounded-lg transition-all shadow-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {busy === tag ? spin : (
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                      </svg>
                    )}
                    {installed ? t("apm.installed") : t("apm.install")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Install form */}
      <form noValidate onSubmit={handleInstall} className="bg-surface border border-border-soft rounded-2xl p-5 space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-text">{t("apm.installPackage")}</h4>
          <p className="text-xs text-muted mt-0.5">{t("apm.installHelp")}</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium text-text mb-1.5">{t("apm.package")}<span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              aria-label={t("apm.package")} value={pkgInput}
              onChange={(e) => setPkgInput(e.target.value)}
              placeholder={t("apm.packageHint")}
              required
              className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-text mb-1.5">{t("apm.ref")}<span className="text-muted font-normal">{t("apm.optional")}</span>
            </label>
            <input
              type="text"
              aria-label={t("apm.ref")} value={refInput}
              onChange={(e) => setRefInput(e.target.value)}
              placeholder="main / v1.2.3 / sha"
              className="w-full px-3 py-2 bg-surface-2 border border-border-soft rounded-xl text-sm text-text font-mono focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-accent transition-all"
            />
          </div>
        </div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
            <input
              type="checkbox"
              checked={devInput}
              onChange={(e) => setDevInput(e.target.checked)}
              className="rounded-sm border-border-soft text-accent focus:ring-accent"
            />
            <span>{t("apm.dev")}</span>
          </label>
          <button
            type="submit"
            disabled={anyBusy || !pkgInput.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm text-accent-fg bg-accent rounded-xl transition-all shadow-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {busy === "install" ? spin : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            )}
            {t("apm.install")}
          </button>
        </div>
      </form>

      {/* Materialised skill dirs */}
      {materialisedDirs.length > 0 && (
        <div className="bg-surface border border-border-soft rounded-2xl p-5">
          <h4 className="text-sm font-semibold text-text mb-2">{t("apm.directories")}</h4>
          <ul className="space-y-1 text-xs font-mono text-text">
            {materialisedDirs.map((d) => <li key={d}>{d}</li>)}
          </ul>
        </div>
      )}

      {/* Last command output (collapsible) */}
      {lastResult && (
        <div className="bg-surface border border-border-soft rounded-2xl overflow-hidden">
          <button
            onClick={() => setOutputOpen((v) => !v)}
            className="w-full flex items-center justify-between px-5 py-3 text-sm font-medium text-text hover:bg-hover transition-colors"
          >
            <div className="flex items-center gap-3">
              <span>{t("apm.output")}</span>
              <span className={`text-[11px] px-2 py-0.5 rounded-full font-mono ${
                lastResult.success
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "bg-red-500/15 text-red-600 dark:text-red-400"
              }`}>
                rc={formatNumber(lastResult.returncode)}
              </span>
              <span className="text-[11px] text-muted font-mono">
                {formatDuration(lastResult.duration_ms)}
              </span>
            </div>
            <svg className={`w-4 h-4 text-slate-400 transition-transform ${outputOpen ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
          {outputOpen && (
            <div className="px-5 pb-5 space-y-3 border-t border-border-soft">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-muted mt-3 mb-1.5">stdout</div>
                <pre className="text-xs font-mono whitespace-pre-wrap max-h-64 overflow-auto bg-surface-2 dark:bg-black/30 border border-border-soft rounded-lg p-3 text-text">
                  {lastResult.stdout || t("apm.emptyOutput")}
                </pre>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wider text-muted mb-1.5">stderr</div>
                <pre className="text-xs font-mono whitespace-pre-wrap max-h-64 overflow-auto bg-surface-2 dark:bg-black/30 border border-border-soft rounded-lg p-3 text-text">
                  {lastResult.stderr || t("apm.emptyOutput")}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
