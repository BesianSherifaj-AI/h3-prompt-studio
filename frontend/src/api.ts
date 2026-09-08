let token = "";
export class ApiError extends Error {
  constructor(message:string, public status:number) { super(message); this.name='ApiError'; }
}
export const setToken = (value: string) => {
  token = value;
};
export async function api(path: string, body?: any, form?: FormData, method?: string) {
  const send = () => fetch("/api" + path, {
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
  if(response.status===403 && path!=='/bootstrap') {
    const failure=await response.clone().json().catch(()=>null);
    // A rejected session performs no mutation. Refresh only the credential,
    // keeping the current in-memory draft, then retry that rejected request once.
    if(failure?.detail==='Studio session expired. Reload this page.') {
      const fresh=await fetch('/api/bootstrap');
      if(fresh.ok) {
        const bootstrap=await fresh.json();
        if(typeof bootstrap.token==='string'&&/^[A-Za-z0-9_-]{32,128}$/.test(bootstrap.token)) {
          setToken(bootstrap.token);response=await send();
        }
      }
    }
  }
  let data: any;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "Studio returned an unreadable response. Check the connection.",
    );
  }
  if (!response.ok)
    throw new ApiError(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data), response.status,
    );
  return data;
}
export const apiPatch = (path: string, body: any) => api(path, body, undefined, "PATCH");
export function downloadText(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
