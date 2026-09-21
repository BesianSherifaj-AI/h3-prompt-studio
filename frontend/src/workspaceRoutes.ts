import { useEffect, useState } from "react";

export type WorkspaceMode = "studio" | "game";
export function resolveWorkspace(href: string, remembered?: string | null): WorkspaceMode {
  const url = new URL(href, "http://localhost");
  if (url.searchParams.has("continue_mmh3") || url.searchParams.has("continue_seed")) return "studio";
  if (url.pathname.replace(/\/$/, "") === "/game") return "game";
  if (url.pathname.replace(/\/$/, "") === "/studio") return "studio";
  if (url.searchParams.has("game")) return "game";
  return remembered === "game" ? "game" : "studio";
}
export function workspaceHref(mode: WorkspaceMode, href: string, preserveAction = false): string {
  const url = new URL(href, "http://localhost");
  url.pathname = "/" + mode;
  if (!preserveAction || !url.searchParams.get("game")) url.searchParams.delete("game");
  if (!preserveAction) {
    for (const key of ["project", "continue_mmh3", "continue_seed"]) url.searchParams.delete(key);
    url.hash = "";
  }
  return url.pathname + url.search + url.hash;
}
function currentWorkspace(): WorkspaceMode {
  let remembered: string | null = null;
  try { remembered = localStorage.getItem("h3-workspace-mode"); } catch { /* Storage is optional. */ }
  return resolveWorkspace(window.location.href, remembered);
}
export function useWorkspaceRoute() {
  const [mode, setMode] = useState(currentWorkspace);
  useEffect(() => {
    const onBack = () => setMode(currentWorkspace());
    window.addEventListener("popstate", onBack);
    window.history.replaceState(window.history.state, "", workspaceHref(currentWorkspace(), window.location.href, true));
    return () => window.removeEventListener("popstate", onBack);
  }, []);
  useEffect(() => {
    document.title = mode === "game" ? "Game · H3 Prompt Studio" : "Studio · H3 Prompt Studio";
    try { localStorage.setItem("h3-workspace-mode", mode); } catch { /* Storage is optional. */ }
  }, [mode]);
  const navigate = (next: WorkspaceMode) => {
    if (next === mode) return;
    window.history.pushState(window.history.state, "", workspaceHref(next, window.location.href));
    setMode(next);
  };
  return [mode, navigate] as const;
}
