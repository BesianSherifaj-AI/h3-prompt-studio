import { useEffect, useState } from "react";
import { api } from "./api";
import "./LiveRenderProgress.css";

type Props = { runId: string };
type Source = { available: boolean; status: string; events_url: string; prompt_id: string | null; node_labels: Record<string, string> };
export type LiveProgress = { label: string; value?: number; maximum?: number; percent?: number };

export function renderProgressEvent(raw: unknown, promptId: string, labels: Record<string, string>): LiveProgress | null {
  if (typeof raw !== "string" || raw.length > 262144) return null;
  let event: any;
  try { event = JSON.parse(raw); } catch { return null; }
  const data = event?.data;
  if (!data || data.prompt_id !== promptId) return null;
  if (event.type === "executing") return data.node == null ? { label: "Finishing the video" } : { label: labels[String(data.node)] || "Preparing the scene" };
  let node = data;
  if (event.type === "progress_state") {
    if (!data.nodes || typeof data.nodes !== "object" || Array.isArray(data.nodes)) return null;
    const entry = Object.entries(data.nodes).find(([, value]: [string, any]) => value?.state === "running" && value?.prompt_id === promptId);
    if (!entry) return null;
    node = { ...(entry[1] as object), node: entry[0] };
  } else if (event.type !== "progress") return null;
  if (typeof node.value !== "number" || typeof node.max !== "number" || !Number.isFinite(node.value) || !Number.isFinite(node.max) || node.value < 0 || node.max <= 0) return null;
  return { label: labels[String(node.node)] || "Preparing the scene", value: Math.min(node.value, node.max), maximum: node.max, percent: Math.round(Math.min(1, node.value / node.max) * 100) };
}

export default function LiveRenderProgress({ runId }: Props) {
  const [progress, setProgress] = useState<LiveProgress>({ label: "Connecting to live render progress…" });
  const [connection, setConnection] = useState("");
  useEffect(() => {
    let disposed = false, socket: EventSource | null = null, retry: ReturnType<typeof setTimeout> | undefined;
    let release: (() => void) | undefined;
    setProgress({ label: "Connecting to live render progress…" }); setConnection("");
    const connect = async () => {
      let source: Source;
      try { source = await api(`/video/runs/${encodeURIComponent(runId)}/live-progress`); }
      catch { if (!disposed) { setConnection("Live progress is temporarily unavailable. Rendering continues."); retry = setTimeout(connect, 5000); } return; }
      if (disposed) return;
      if (!source.available || !source.events_url || !source.prompt_id) {
        setProgress({ label: ["succeeded", "failed"].includes(source.status) ? "This render has finished" : "Waiting for the render to begin…" });
        if (!["succeeded", "failed"].includes(source.status)) retry = setTimeout(connect, 2000);
        return;
      }
      const observe = () => new Promise<void>(resolve => {
        release = resolve;
        if (disposed) { resolve(); return; }
        try { socket = new EventSource(source.events_url); }
        catch { setConnection("Live progress could not connect. Rendering continues."); resolve(); return; }
        socket.onopen = () => { if (!disposed) { setConnection(""); setProgress({ label: "Waiting for ComfyUI's next progress update…" }); } };
        socket.onmessage = event => {
          if (disposed) return;
          const next = renderProgressEvent(event.data, source.prompt_id!, source.node_labels || {});
          if (next) { setProgress(next); setConnection(""); }
        };
        const reconnect = () => { socket?.close(); resolve(); if (!disposed) retry = setTimeout(connect, 4000); };
        socket.onerror = () => { if (!disposed) setConnection("Reconnecting live progress. Your render continues."); reconnect(); };
        socket.addEventListener("unavailable", () => { if (!disposed) setConnection("Live progress is temporarily unavailable. Rendering continues."); reconnect(); });
        socket.addEventListener("finished", () => { socket?.close(); resolve(); if (!disposed) { setProgress({ label: "This render has finished" }); setConnection(""); } });
      });
      if (navigator.locks) {
        await navigator.locks.request(`h3-live-progress-${runId}`, { ifAvailable: true }, async lock => {
          if (disposed) return;
          if (!lock) { setConnection("Live progress is open in another Studio tab. Your render continues here too."); retry = setTimeout(connect, 4000); return; }
          await observe();
        });
      } else {
        setConnection("Keep live progress open in one Studio tab.");
        await observe();
      }
    };
    void connect();
    return () => { disposed = true; if (retry) clearTimeout(retry); socket?.close(); release?.(); };
  }, [runId]);
  return <section className="live-render-progress" aria-label="Live render progress">
    <div><strong role="status">{progress.label}</strong>{progress.percent != null && <span>{progress.value} / {progress.maximum} · {progress.percent}%</span>}</div>
    {progress.percent != null ? <progress value={progress.percent} max={100} aria-label={progress.label} /> : <progress aria-label={progress.label} />}
    <p>{connection || "Progress describes the current operation, not the whole render. Your video appears here when rendering finishes."}</p>
    <small>Intermediate image previews are not shown.</small>
  </section>;
}
