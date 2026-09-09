import { useEffect, useState } from "react";
import { api } from "./api";

export default function UpscaleButton({ runId }: { runId?: string }) {
  const [info, setInfo] = useState<any>(null), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  useEffect(() => { let active = true; void api("/integrations/upscale").then(v => { if (active) setInfo(v); }).catch(() => {}); return () => { active = false; }; }, []);
  async function open(withVideo: boolean) {
    setBusy(true); setMessage("");
    try { const result = await api("/integrations/upscale/open", withVideo && runId ? { run_id: runId } : {}); setMessage(result.message); }
    catch (e) { setMessage((e as Error).message); } finally { setBusy(false); }
  }
  return <details className="game-clip-details"><summary>UPSCALE · external video tools</summary>
    <p>Use the existing UPSCALE window for scale, frame rate and quality. Opening it adds your video without starting processing.</p>
    <div className="game-button-row"><button disabled={busy || !info?.available || !runId} onClick={() => void open(true)}>Send this scene to UPSCALE</button><button disabled={busy || !info?.available} onClick={() => void open(false)}>Open UPSCALE</button></div>
    {info && !info.available && <p>{info.reason}</p>}<small>This integration accepts videos. Your original clips remain available in Studio.</small>
    {message && <p role="status">{message}</p>}
  </details>;
}
