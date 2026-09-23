import { en, type TranslationKey } from "./i18n";

export type ErrorCode = {
  [K in TranslationKey]: K extends `error.${infer Code}` ? Code : never
}[TranslationKey];

export function errorCode(value: unknown, fallback: ErrorCode = "REQUEST_FAILED"): ErrorCode {
  if (value instanceof ApplicationError) return value.code;
  if (typeof value === "string" && Object.hasOwn(en, `error.${value}`)) return value as ErrorCode;
  return fallback;
}

export class ApplicationError extends Error {
  constructor(public readonly code: ErrorCode, public readonly status?: number) {
    super(code);
  }
}

export async function responseError(response: Response, fallback: ErrorCode): Promise<ApplicationError> {
  let code = fallback;
  if (response.status === 401 || response.status === 403) code = "AUTH_REQUIRED";
  else if (response.status === 422) code = "INVALID_REQUEST";
  try {
    const body = await response.json();
    code = errorCode(body?.code ?? body?.detail?.code, code);
  } catch {
    // Non-JSON failures still get status-based, safe guidance.
  }
  return new ApplicationError(code, response.status);
}
