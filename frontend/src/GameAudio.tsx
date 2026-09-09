import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Asset, Project } from "./model";

export function GameVoiceInput({ onUpload, onAsset, onText }: { onUpload: (files: File[]) => Promise<Asset[]>; onAsset: (asset: Asset, reference: boolean) => void; onText: (text: string) => void }) {
  const [recording, setRecording] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [reference, setReference] = useState(false), [recorded, setRecorded] = useState<Asset | null>(null);
  const recorder = useRef<MediaRecorder | null>(null), stream = useRef<MediaStream | null>(null), mounted = useRef(true), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef({ onUpload, onAsset, onText, reference }); latest.current = { onUpload, onAsset, onText, reference };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; if (timer.current) clearTimeout(timer.current); if (recorder.current?.state === "recording") recorder.current.stop(); stream.current?.getTracks().forEach(track => track.stop()); }; }, []);
  const transcript = async (asset: Asset) => { setBusy(true); setError(""); try { const result = await api("/audio/transcribe", { asset_id: asset.id, options: { model: "small", cpu_threads: 4 } }); if (mounted.current) latest.current.onText(result.text); } catch(e) { if (mounted.current) setError((e as Error).message); } finally { if (mounted.current) setBusy(false); } };
  const start = async () => {
    setError(""); setBusy(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("Microphone recording is unavailable in this browser. Upload an audio file in Photos & sound instead.");
      const audio = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current) { audio.getTracks().forEach(track => track.stop()); return; }
      stream.current = audio;
      const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(type => MediaRecorder.isTypeSupported(type));
      const active = new MediaRecorder(audio, mimeType ? { mimeType } : undefined), chunks: Blob[] = [];
      recorder.current = active;
      active.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      active.onstop = async () => {
        if (timer.current) clearTimeout(timer.current);
        audio.getTracks().forEach(track => track.stop());
        if (!mounted.current) return;
        setRecording(false); setBusy(true);
        try {
          const type = active.mimeType || "audio/webm", file = new File(chunks, `My-speech-${Date.now()}.${type.includes("mp4") ? "m4a" : "webm"}`, { type });
          const assets = await latest.current.onUpload([file]);
          if (!mounted.current) return;
          if (!assets[0]) throw new Error("The recording was not saved. Try uploading a recording in Photos & sound.");
          setRecorded(assets[0]); latest.current.onAsset(assets[0], latest.current.reference); await transcript(assets[0]);
        } catch(e) { if (mounted.current) { setError((e as Error).message); setBusy(false); } }
      };
      active.start(); setRecording(true); setBusy(false);
      timer.current = setTimeout(() => { if (active.state === "recording") active.stop(); }, 115000);
    } catch(e) { stream.current?.getTracks().forEach(track => track.stop()); setError((e as Error).message); setBusy(false); }
  };
  return <details className="game-voice-input"><summary>Speak instead of typing</summary><p className="game-help">Record, stop, then review the transcript in your message box. This never sends a move automatically.</p><label className="game-checkbox"><input type="checkbox" checked={reference} onChange={e => setReference(e.target.checked)}/><span>Also connect recording as an H3 audio reference · choose 2–15 seconds in Photos & sound</span></label><button type="button" disabled={busy} onClick={() => recording ? recorder.current?.stop() : void start()}>{recording ? "Stop & transcribe" : busy ? "Saving / transcribing…" : "Record my speech"}</button>{recorded && <><audio controls src={`/api/assets/${encodeURIComponent(recorded.id)}/file`}/><button type="button" disabled={busy || recording} onClick={() => void transcript(recorded)}>Retry transcription</button></>}{error && <p role="alert">{error}</p>}</details>;
}

export function GameSoundtrack({ runId, project }: { runId: string; project: Project }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [url, setUrl] = useState("");
  const request = useRef({ signature: "", id: "" });
  const selected = project.assets.filter(a => ["audio", "video"].includes(a.media_type) && a.audio_use === "soundtrack" && a.enabled !== false);
  if (!selected.length) return null;
  const mix = async () => {
    const tracks = selected.map(a => ({ asset_id: a.id, start_seconds: 0, end_seconds: null, offset_seconds: 0, gain: 1, fade_in: 0, fade_out: 0, duck: false, ...(project.soundtrack_tracks || []).find((t: any) => t.asset_id === a.id) }));
    const signature = JSON.stringify({ runId, tracks });
    if (request.current.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    setBusy(true); setError("");
    try { const result = await api(`/video/runs/${encodeURIComponent(runId)}/soundtrack`, { tracks, request_id: request.current.id }); setUrl(result.video_url); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <details className="game-clip-details"><summary>Add your soundtrack · {selected.length} {selected.length === 1 ? "layer" : "layers"}</summary><p>Mix the connected soundtrack files into this scene. The original generated audio is retained, and your original video stays saved.</p><button disabled={busy} onClick={() => void mix()}>{busy ? "Mixing soundtrack…" : "Create soundtrack version"}</button>{error && <p role="alert">{error}</p>}{url && <><video controls src={url} style={{ width: "100%", maxHeight: 360 }}/><a href={url} download>Save scene with soundtrack</a></>}</details>;
}
