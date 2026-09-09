let token = "";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export class ApiTimeoutError extends Error {
  constructor() {
    super(
      "Studio did not respond in time. Check the saved request before trying again; work may still be running.",
    );
    this.name = "ApiTimeoutError";
  }
}
export type ApiOptions = { timeoutMs?: number; signal?: AbortSignal };
export const setToken = (value: string) => {
  token = value;
};
export async function api(
  path: string,
  body?: any,
  form?: FormData,
  method?: string,
  options: ApiOptions = {},
) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  const timer =
    options.timeoutMs && options.timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, options.timeoutMs)
      : undefined;
  try {
    const send = () =>
      fetch("/api" + path, {
        signal: controller.signal,
        method: method || (body !== undefined || form ? "POST" : "GET"),
        headers: {
          ...(form
            ? {}
            : body !== undefined
              ? { "Content-Type": "application/json" }
              : {}),
          "X-H3-Token": token,
        },
        body: form || (body !== undefined ? JSON.stringify(body) : undefined),
      });
    let response = await send();
    if (response.status === 403 && path !== "/bootstrap") {
      const failure = await response
        .clone()
        .json()
        .catch(() => null);
      // A rejected session performs no mutation. Refresh only the credential,
      // keeping the current in-memory draft, then retry that rejected request once.
      if (failure?.detail === "Studio session expired. Reload this page.") {
        const fresh = await fetch("/api/bootstrap", {
          signal: controller.signal,
        });
        if (fresh.ok) {
          const bootstrap = await fresh.json();
          if (
            typeof bootstrap.token === "string" &&
            /^[A-Za-z0-9_-]{32,128}$/.test(bootstrap.token)
          ) {
            setToken(bootstrap.token);
            response = await send();
          }
        }
      }
    }
    let data: any;
    try {
      data = await response.json();
    } catch (error) {
      if (controller.signal.aborted) throw error;
      const message =
        "Studio returned an unreadable response. Check the connection.";
      if (!response.ok) throw new ApiError(message, response.status);
      throw new Error(message);
    }
    if (!response.ok)
      throw new ApiError(
        typeof data?.detail === "string"
          ? data.detail
          : JSON.stringify(data?.detail || data) ||
              `Studio returned HTTP ${response.status}.`,
        response.status,
      );
    return data;
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError();
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
export const apiPatch = (path: string, body: any) =>
  api(path, body, undefined, "PATCH");
export function downloadText(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
