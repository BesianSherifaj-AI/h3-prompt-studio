import { Clapperboard, Gamepad2, HelpCircle, Plug, Video } from "lucide-react";
import type { WorkspaceMode } from "./workspaceRoutes";

export default function WorkspaceNavigation({ mode, onNavigate, onConnections, onHelp }: {
  mode: WorkspaceMode; onNavigate: (mode: WorkspaceMode) => void;
  onConnections: () => void; onHelp: () => void;
}) {
  return <header className={`workspace-mode-bar workspace-${mode}`}>
    <a className="workspace-skip" href={`#${mode}-workspace`}>Skip to {mode}</a>
    <div className="workspace-brand"><span>H3</span><strong>Prompt Studio<small>LOCAL CREATIVE WORKSPACE</small></strong></div>
    <nav className="workspace-switch" aria-label="Workspace mode">
      {(["video", "studio", "game"] as const).map(value => <a key={value} href={`/${value}`} aria-current={mode === value ? "page" : undefined}
        onClick={event => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onNavigate(value); } }}>
        {value === "video" ? <Video size={18}/> : value === "studio" ? <Clapperboard size={18}/> : <Gamepad2 size={18}/>}
        <span>{value === "video" ? "Video" : value === "studio" ? "Studio" : "Game"}<small>{value === "video" ? "One clip · up to 15s" : value === "studio" ? "Connected films · 1–10 min" : "Play & explore"}</small></span>
      </a>)}
    </nav>
    <div className="workspace-utilities"><button onClick={onConnections}><Plug size={16}/><span>Connections</span></button><button onClick={onHelp}><HelpCircle size={16}/><span>Help</span></button></div>
  </header>;
}
