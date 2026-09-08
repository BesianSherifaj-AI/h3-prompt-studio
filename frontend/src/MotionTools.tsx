import { useEffect, useId, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { ArrowUpRight, Camera, Download, ExternalLink, ImagePlus, Pencil, Square, Trash2, Undo2 } from 'lucide-react';
import { creativeResources } from './resources';
import './MotionTools.css';

type Props = {
  duration: number;
  subjects: { id: string; name: string }[];
  onInstructions: (text: string) => void;
  onReference: (file: File) => Promise<void> | void;
};
export type FloorPoint = { x: number; z: number };
export type View = 'top' | 'iso';
type ScreenPoint = { x: number; y: number };
type Tool = 'pen' | 'arrow' | 'rect';
type Stroke = { tool: Tool; color: string; width: number; points: ScreenPoint[] };
type Marker = 'subjectStart' | 'subjectEnd' | 'cameraStart' | 'cameraEnd';
export type MovementPlan = {
  subjectStart: FloorPoint; subjectEnd: FloorPoint;
  cameraStart: FloorPoint; cameraEnd: FloorPoint;
  path: 'straight' | 'arc'; cameraPath: 'straight' | 'arc';
  pace: 'steady' | 'ease' | 'quick'; duration: number;
};
const W = 736, H = 416;
const labels: Record<Marker, string> = {
  subjectStart: 'Subject start', subjectEnd: 'Subject end', cameraStart: 'Camera start', cameraEnd: 'Camera end',
};
const markerKeys = Object.keys(labels) as Marker[];
const colors = { subject: '#7c3aed', camera: '#0369a1' };

export function validDuration(value: number): number {
  // Individual timeline shots can be shorter than H3's overall 4-second minimum.
  return Number.isFinite(value) && value > 0 ? Math.min(15, value) : 5;
}
export function clampPoint(p: FloorPoint): FloorPoint {
  const clamp = (n: number) => Math.round(Math.max(0, Math.min(10, Number.isFinite(n) ? n : 0)) * 10) / 10;
  return { x: clamp(p.x), z: clamp(p.z) };
}
/** Scene X increases right; Z increases toward the back of the floor. These are layout units. */
export function projectPoint(p: FloorPoint, view: View): ScreenPoint {
  return view === 'top' ? { x: 188 + p.x * 36, y: 382 - p.z * 32 }
    : { x: 368 + (p.x - p.z) * 29, y: 380 - (p.x + p.z) * 14 };
}
export function unprojectPoint(p: ScreenPoint, view: View): FloorPoint {
  if (view === 'top') return { x: (p.x - 188) / 36, z: (382 - p.y) / 32 };
  const sum = (380 - p.y) / 14, diff = (p.x - 368) / 29;
  return { x: (sum + diff) / 2, z: (sum - diff) / 2 };
}
export function dragFloorPoint(pointer: ScreenPoint, grabOffset: ScreenPoint, view: View): FloorPoint {
  return clampPoint(unprojectPoint({ x: pointer.x - grabOffset.x, y: pointer.y - grabOffset.y }, view));
}
export function arcControl(a: FloorPoint, b: FloorPoint): FloorPoint {
  // Keep the quadratic control point on the floor; the entire curve stays inside its convex bounds.
  return clampPoint({ x: (a.x + b.x) / 2 - (b.z - a.z) * .35, z: (a.z + b.z) / 2 + (b.x - a.x) * .35 });
}
export function movementPath(a: FloorPoint, b: FloorPoint, kind: MovementPlan['path'], view: View): string {
  const start = projectPoint(a, view), end = projectPoint(b, view);
  if (kind === 'straight') return `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
  const control = projectPoint(arcControl(a, b), view);
  return `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`;
}
export function defaultPlan(duration: number): MovementPlan {
  return { subjectStart: { x: 2, z: 6 }, subjectEnd: { x: 7, z: 6 }, cameraStart: { x: 3, z: 1 },
    cameraEnd: { x: 6, z: 2 }, path: 'straight', cameraPath: 'straight', pace: 'ease', duration: validDuration(duration) };
}
export function buildMovementInstructions(plan: MovementPlan, subject: string): string {
  const point = (p: FloorPoint) => `(X ${clampPoint(p).x}, Z ${clampPoint(p).z})`;
  const stationary = (a: FloorPoint, b: FloorPoint) => a.x === b.x && a.z === b.z;
  const describe = (who: string, a: FloorPoint, b: FloorPoint, kind: MovementPlan['path']) => {
    if (stationary(a, b)) return `${who} stays at ${point(a)} throughout.`;
    const path = kind === 'straight' ? 'a straight path' : `a gentle curved path bending toward ${point(arcControl(a, b))}`;
    return `${who} starts at ${point(a)}, follows ${path}, and ends at ${point(b)}.`;
  };
  const timing = stationary(plan.subjectStart, plan.subjectEnd) && stationary(plan.cameraStart, plan.cameraEnd)
    ? 'Hold these subject and camera positions for the full shot duration.' : plan.pace === 'quick'
    ? `Complete the movement by ${Number((validDuration(plan.duration) * .65).toPrecision(6))} seconds, then hold the final positions for the remainder.`
    : plan.pace === 'ease' ? 'Use the full shot duration, easing into the movement and easing to a stop at the end.'
      : 'Travel at a steady pace over the full shot duration and stop at the final positions.';
  return [
    `Movement layout for one ${validDuration(plan.duration)}-second continuous shot:`,
    'Use the floor plan as a relative spatial guide: X runs from left (0) to right (10), and Z runs from front (0) to back (10). These are scene directions, not screen directions or measured metres.',
    describe(subject.trim() || 'The subject', plan.subjectStart, plan.subjectEnd, plan.path),
    describe('The camera', plan.cameraStart, plan.cameraEnd, plan.cameraPath),
    'Keep the camera at a constant eye-level height, aimed at the subject as it moves; avoid unintended zooms or cuts.',
    timing,
  ].join('\n');
}

function drawSketch(canvas: HTMLCanvasElement, strokes: Stroke[]) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);
  for (const stroke of strokes) {
    const a = stroke.points[0], b = stroke.points[stroke.points.length - 1];
    if (!a || !b) continue;
    ctx.strokeStyle = stroke.color; ctx.fillStyle = stroke.color; ctx.lineWidth = stroke.width;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath();
    if (stroke.tool === 'rect') { ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); continue; }
    ctx.moveTo(a.x, a.y);
    if (stroke.tool === 'pen') {
      if (stroke.points.length === 1) { ctx.arc(a.x, a.y, stroke.width / 2, 0, Math.PI * 2); ctx.fill(); }
      else { for (const p of stroke.points.slice(1)) ctx.lineTo(p.x, p.y); ctx.stroke(); }
    } else {
      ctx.lineTo(b.x, b.y); ctx.stroke();
      const angle = Math.atan2(b.y - a.y, b.x - a.x), size = 12 + stroke.width;
      ctx.beginPath(); ctx.moveTo(b.x, b.y);
      ctx.lineTo(b.x - size * Math.cos(angle - Math.PI / 6), b.y - size * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(b.x - size * Math.cos(angle + Math.PI / 6), b.y - size * Math.sin(angle + Math.PI / 6));
      ctx.closePath(); ctx.fill();
    }
  }
}
async function pngFile(canvas: HTMLCanvasElement, name: string): Promise<File> {
  const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(new Error('Could not export the PNG.')), 'image/png'));
  return new File([blob], name, { type: 'image/png' });
}
function downloadFile(file: File) {
  const url = URL.createObjectURL(file), link = document.createElement('a');
  link.href = url; link.download = file.name; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function svgFile(svg: SVGSVGElement): Promise<File> {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); clone.setAttribute('width', String(W)); clone.setAttribute('height', String(H));
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }));
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => { img.onload = () => resolve(); img.onerror = () => reject(new Error('Could not render the movement guide.')); img.src = url; });
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('Canvas export is unavailable.');
    ctx.drawImage(img, 0, 0, W, H); return await pngFile(canvas, 'h3-movement-layout.png');
  } finally { URL.revokeObjectURL(url); }
}

export default function MotionTools({ duration, subjects, onInstructions, onReference }: Props) {
  const [tab, setTab] = useState<'sketch' | 'movement' | 'resources'>('sketch');
  const [tool, setTool] = useState<Tool>('pen'), [color, setColor] = useState('#272334'), [width, setWidth] = useState(4);
  const [history, setHistory] = useState<Stroke[][]>([[]]), [draft, setDraft] = useState<Stroke | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null), strokeRef = useRef<Stroke | null>(null), sketchPointer = useRef<number | null>(null);
  const [plan, setPlan] = useState(() => defaultPlan(duration));
  const [durationText, setDurationText] = useState(String(validDuration(duration)));
  const [subjectId, setSubjectId] = useState(subjects[0]?.id || '');
  const [view, setView] = useState<View>('iso'), [selected, setSelected] = useState<Marker>('subjectStart');
  const [instructionEdit, setInstructionEdit] = useState<{ source: string; text: string } | null>(null);
  const [status, setStatus] = useState(''), [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null), drag = useRef<{ key: Marker; pointer: number; offset: ScreenPoint } | null>(null);
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const subjectName = subjects.find(s => s.id === subjectId)?.name || subjects[0]?.name || 'The subject';
  const generatedInstructions = buildMovementInstructions(plan, subjectName);
  const instructions = instructionEdit?.source === generatedInstructions ? instructionEdit.text : generatedInstructions;
  const strokes = history[history.length - 1];
  useEffect(() => { setPlan(p => ({ ...p, duration: validDuration(duration) })); }, [duration]);
  useEffect(() => { setDurationText(String(plan.duration)); }, [plan.duration]);
  useEffect(() => { if (!subjects.some(s => s.id === subjectId)) setSubjectId(subjects[0]?.id || ''); }, [subjects, subjectId]);
  useEffect(() => { if (canvasRef.current) drawSketch(canvasRef.current, draft ? [...strokes, draft] : strokes); }, [strokes, draft, tab]);

  const pushHistory = (next: Stroke[]) => setHistory(h => [...h.slice(-39), next]);
  const sketchPoint = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(W, (e.clientX - box.left) * W / box.width)), y: Math.max(0, Math.min(H, (e.clientY - box.top) * H / box.height)) };
  };
  const finishSketch = (e: ReactPointerEvent<HTMLCanvasElement>, cancel = false) => {
    if (sketchPointer.current !== e.pointerId) return;
    if (strokeRef.current && !cancel) pushHistory([...strokes, strokeRef.current]);
    sketchPointer.current = null; strokeRef.current = null; setDraft(null);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };
  const exportImage = async (kind: 'sketch' | 'movement', send: boolean) => {
    setBusy(true); setStatus('');
    try {
      const file = kind === 'sketch' ? await pngFile(canvasRef.current!, 'h3-sketch-reference.png') : await svgFile(svgRef.current!);
      if (send) { await onReference(file); setStatus('PNG added to your reference library. Choose its role and approve its description before use.'); }
      else { downloadFile(file); setStatus('PNG download started.'); }
    } catch (error) { setStatus(error instanceof Error ? error.message : 'The image could not be exported.'); }
    finally { setBusy(false); }
  };
  const setMarker = (key: Marker, p: FloorPoint) => setPlan(current => ({ ...current, [key]: clampPoint(p) }));
  const dragMarker = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag.current || drag.current.pointer !== e.pointerId) return;
    const svg = e.currentTarget, matrix = svg.getScreenCTM(); if (!matrix) return;
    const point = svg.createSVGPoint(); point.x = e.clientX; point.y = e.clientY;
    const local = point.matrixTransform(matrix.inverse());
    setMarker(drag.current.key, dragFloorPoint(local, drag.current.offset, view));
  };
  const endDrag = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (drag.current?.pointer === e.pointerId) { drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }
  };
  const selectedPoint = plan[selected];

  return <div className="h3-motion-tools">
    <div className="h3-tools-tabs" aria-label="Creative tools">
      {(['sketch', 'movement', 'resources'] as const).map(key => <button type="button" key={key} aria-pressed={tab === key} onClick={() => { setTab(key); setStatus(''); }}>
        {key === 'sketch' ? 'Sketch reference' : key === 'movement' ? 'Movement layout' : 'Free resources'}
      </button>)}
    </div>

    {tab === 'sketch' && <section className="h3-tool-section" aria-label="Sketch reference">
      <div className="h3-tool-heading"><div><h3>Draw the idea</h3><p>Block out a pose, an object or a camera direction. Export a 736 × 416 PNG.</p></div><span className="h3-tool-badge">Local drawing</span></div>
      <div className="h3-sketch-toolbar">
        <div className="h3-tool-segment" aria-label="Drawing tools">
          <button type="button" aria-pressed={tool === 'pen'} onClick={() => setTool('pen')}><Pencil size={16} /> Pen</button>
          <button type="button" aria-pressed={tool === 'arrow'} onClick={() => setTool('arrow')}><ArrowUpRight size={16} /> Arrow</button>
          <button type="button" aria-pressed={tool === 'rect'} onClick={() => setTool('rect')}><Square size={16} /> Rectangle</button>
        </div>
        <label className="h3-inline-control">Color <input aria-label="Drawing color" type="color" value={color} onChange={e => setColor(e.target.value)} /></label>
        <label className="h3-inline-control">Width <input aria-label="Pen width" type="range" min="1" max="16" value={width} onChange={e => setWidth(Number(e.target.value))} /><span>{width}</span></label>
        <button type="button" disabled={history.length <= 1} onClick={() => setHistory(h => h.slice(0, -1))}><Undo2 size={16} /> Undo</button>
        <button type="button" disabled={!strokes.length} onClick={() => pushHistory([])}><Trash2 size={16} /> Clear</button>
      </div>
      <canvas ref={canvasRef} className="h3-sketch-canvas" width={W} height={H} aria-label="Sketch canvas. Drag to draw with the selected tool."
        onPointerDown={e => {
          if (e.button !== 0 || sketchPointer.current !== null) return;
          e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); sketchPointer.current = e.pointerId;
          strokeRef.current = { tool, color, width, points: [sketchPoint(e)] }; setDraft(strokeRef.current);
        }}
        onPointerMove={e => {
          const current = strokeRef.current; if (!current || sketchPointer.current !== e.pointerId) return;
          const p = sketchPoint(e), last = current.points[current.points.length - 1];
          if (Math.hypot(p.x - last.x, p.y - last.y) < .8) return;
          strokeRef.current = { ...current, points: current.tool === 'pen' ? [...current.points, p] : [current.points[0], p] };
          setDraft(strokeRef.current);
        }} onPointerUp={e => finishSketch(e)} onPointerCancel={e => finishSketch(e, true)} onLostPointerCapture={e => finishSketch(e, true)} />
      <div className="h3-tool-actions"><span>Undo also restores a cleared sketch. Drawings stay here while this panel is open.</span>
        <button type="button" disabled={busy || !strokes.length} onClick={() => exportImage('sketch', false)}><Download size={16} /> Export PNG</button>
        <button type="button" className="h3-tool-primary" disabled={busy || !strokes.length} onClick={() => exportImage('sketch', true)}><ImagePlus size={16} /> Add PNG to references</button>
      </div>
    </section>}

    {tab === 'movement' && <section className="h3-tool-section" aria-label="Movement layout">
      <div className="h3-tool-heading"><div><h3>Plan a move</h3><p>Drag the start and end markers on a 10 × 10 floor. Both views show the same layout.</p></div><span className="h3-tool-badge">Layout guide</span></div>
      <div className="h3-movement-controls">
        <label>Subject<select value={subjectId} onChange={e => setSubjectId(e.target.value)}>{subjects.length ? subjects.map(s => <option key={s.id} value={s.id}>{s.name || 'Unnamed subject'}</option>) : <option value="">The subject</option>}</select></label>
        <label>Shot duration<input type="number" aria-label="Movement duration in seconds" min=".001" max="15" step=".001" value={durationText}
          onChange={e => { setDurationText(e.target.value); const n = e.target.valueAsNumber; if (n > 0 && n <= 15) setPlan(p => ({ ...p, duration: n })); }}
          onBlur={() => { const n = validDuration(durationText.trim() ? Number(durationText) : NaN); setDurationText(String(n)); setPlan(p => ({ ...p, duration: n })); }} /></label>
        <label>Subject path<select value={plan.path} onChange={e => setPlan(p => ({ ...p, path: e.target.value as MovementPlan['path'] }))}><option value="straight">Straight</option><option value="arc">Gentle arc</option></select></label>
        <label>Camera path<select value={plan.cameraPath} onChange={e => setPlan(p => ({ ...p, cameraPath: e.target.value as MovementPlan['path'] }))}><option value="straight">Straight</option><option value="arc">Gentle arc</option></select></label>
        <label>Pace<select value={plan.pace} onChange={e => setPlan(p => ({ ...p, pace: e.target.value as MovementPlan['pace'] }))}><option value="ease">Ease in / out</option><option value="steady">Steady</option><option value="quick">Quick move, then hold</option></select></label>
      </div>
      <div className="h3-plan-toolbar"><div className="h3-tool-segment" aria-label="Layout view">
        <button type="button" aria-pressed={view === 'top'} onClick={() => setView('top')}>Top-down</button>
        <button type="button" aria-pressed={view === 'iso'} onClick={() => setView('iso')}>Isometric 3D</button>
      </div><span><i className="h3-legend-subject" /> Subject <i className="h3-legend-camera" /> Camera · filled = start, outlined = end</span></div>
      <svg ref={svgRef} className="h3-movement-svg" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Draggable movement floor plan"
        onPointerMove={dragMarker} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag}>
        <rect width={W} height={H} fill="#f8f9fc" />
        <text x="20" y="25" fontFamily="Arial, sans-serif" fontSize="13" fontWeight="700" fill="#263247">{`${subjectName.slice(0, 50)} · ${plan.duration}s · ${view === 'iso' ? 'Isometric' : 'Top-down'} layout`}</text>
        <text x="20" y="44" fontFamily="Arial, sans-serif" fontSize="11" fill="#536177">Guide only · no motion simulation or movie</text>
        <defs>{(['subject', 'camera'] as const).map(key => <marker key={key} id={`${id}-${key}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 8 4 L 0 8 z" fill={colors[key]} /></marker>)}</defs>
        <polygon points={[{ x: 0, z: 0 }, { x: 10, z: 0 }, { x: 10, z: 10 }, { x: 0, z: 10 }].map(p => { const s = projectPoint(p, view); return `${s.x},${s.y}`; }).join(' ')} fill="#edf0f6" stroke="#8997ad" strokeWidth="1.2" />
        {Array.from({ length: 11 }, (_, i) => [
          [projectPoint({ x: i, z: 0 }, view), projectPoint({ x: i, z: 10 }, view)],
          [projectPoint({ x: 0, z: i }, view), projectPoint({ x: 10, z: i }, view)],
        ].map(([a, b], j) => <line key={`${i}-${j}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#cbd3df" strokeWidth=".8" />))}
        <text x={projectPoint({ x: 5, z: 0 }, view).x} y={projectPoint({ x: 5, z: 0 }, view).y + 20} textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="11" fill="#536177">X → right · Z → back</text>
        <path d={movementPath(plan.subjectStart, plan.subjectEnd, plan.path, view)} stroke={colors.subject} strokeWidth="3" strokeDasharray="7 5" fill="none" markerEnd={`url(#${id}-subject)`} />
        <path d={movementPath(plan.cameraStart, plan.cameraEnd, plan.cameraPath, view)} stroke={colors.camera} strokeWidth="3" strokeDasharray="7 5" fill="none" markerEnd={`url(#${id}-camera)`} />
        {markerKeys.map(key => {
          const p = projectPoint(plan[key], view), camera = key.startsWith('camera'), end = key.endsWith('End'), ink = camera ? colors.camera : colors.subject;
          const tall = view === 'iso' ? (camera ? 26 : 37) : 0;
          return <g key={key} role="button" tabIndex={0} aria-label={`${labels[key]}, X ${plan[key].x}, Z ${plan[key].z}. Arrow keys move by 0.1 units; Shift by one unit.`}
            onFocus={() => setSelected(key)}
            onKeyDown={e => {
              const delta = e.shiftKey ? 1 : .1, shift: Record<string, FloorPoint> = { ArrowLeft: { x: -delta, z: 0 }, ArrowRight: { x: delta, z: 0 }, ArrowUp: { x: 0, z: delta }, ArrowDown: { x: 0, z: -delta } };
              if (shift[e.key]) { e.preventDefault(); setMarker(key, { x: plan[key].x + shift[e.key].x, z: plan[key].z + shift[e.key].z }); }
            }}
            onPointerDown={e => {
              const svg = svgRef.current, matrix = svg?.getScreenCTM(); if (e.button !== 0 || drag.current || !svg || !matrix) return;
              e.preventDefault(); setSelected(key); const point = svg.createSVGPoint(); point.x = e.clientX; point.y = e.clientY;
              const local = point.matrixTransform(matrix.inverse());
              drag.current = { key, pointer: e.pointerId, offset: { x: local.x - p.x, y: local.y - p.y } }; svg.setPointerCapture(e.pointerId);
            }}>
            <title>{labels[key]} — drag or use arrow keys</title>
            <circle cx={p.x} cy={p.y} r="19" fill="transparent" />
            {tall > 0 && <><line x1={p.x} y1={p.y} x2={p.x} y2={p.y - tall} stroke={ink} strokeWidth="3" /><ellipse cx={p.x} cy={p.y + 2} rx="10" ry="4" fill={ink} opacity=".16" /></>}
            <circle cx={p.x} cy={p.y - tall} r="12" fill={end ? '#ffffff' : ink} stroke={ink} strokeWidth="2.5" />
            {selected === key && <circle cx={p.x} cy={p.y - tall} r="16" fill="none" stroke={ink} strokeWidth="1" strokeDasharray="3 3" />}
            <text x={p.x} y={p.y - tall + 4} textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="10" fontWeight="700" fill={end ? ink : '#ffffff'}>{camera ? 'C' : 'S'}{end ? '2' : '1'}</text>
            <text x={p.x > 590 ? p.x - 20 : p.x + 20} y={p.y - tall + 4} textAnchor={p.x > 590 ? 'end' : 'start'} fontFamily="Arial, sans-serif" fontSize="11" fontWeight="600" fill={ink} stroke="#f8f9fc" strokeWidth="3" paintOrder="stroke">{labels[key]}</text>
          </g>;
        })}
      </svg>
      <div className="h3-position-controls">
        <label>Selected marker<select value={selected} onChange={e => setSelected(e.target.value as Marker)}>{markerKeys.map(key => <option key={key} value={key}>{labels[key]}</option>)}</select></label>
        <label>X · left / right<input type="number" min="0" max="10" step=".1" value={selectedPoint.x} onChange={e => setMarker(selected, { ...selectedPoint, x: e.target.valueAsNumber })} /></label>
        <label>Z · front / back<input type="number" min="0" max="10" step=".1" value={selectedPoint.z} onChange={e => setMarker(selected, { ...selectedPoint, z: e.target.valueAsNumber })} /></label>
        <button type="button" onClick={() => setPlan(p => ({ ...p, cameraEnd: { ...p.cameraStart } }))}><Camera size={16} /> Static camera</button>
        <button type="button" onClick={() => setPlan(defaultPlan(duration))}>Reset layout</button>
      </div>
      <p className="h3-tool-note">This is a spatial layout guide with a fixed eye-level camera, not a motion simulation, keyframe control or rendered movie. The timing is a prompt instruction. Adding it does not retime your project. PNG guides contain labels and arrows; set their role deliberately.</p>
      <label className="h3-instructions-label">Editable movement instructions<textarea value={instructions} rows={7} onChange={e => setInstructionEdit({ source: generatedInstructions, text: e.target.value })} /></label>
      <div className="h3-tool-actions"><span>Changing the layout refreshes the draft instructions.</span>
        <button type="button" disabled={busy} onClick={() => exportImage('movement', false)}><Download size={16} /> Export guide PNG</button>
        <button type="button" disabled={busy} onClick={() => exportImage('movement', true)}><ImagePlus size={16} /> Add guide to references</button>
        <button type="button" className="h3-tool-primary" disabled={!instructions.trim()} onClick={() => { onInstructions(instructions); setStatus('Movement instructions added to the project.'); }}>Use instructions</button>
      </div>
    </section>}

    {tab === 'resources' && <section className="h3-tool-section" aria-label="Free creative resources">
      <div className="h3-tool-heading"><div><h3>Build a richer reference</h3><p>Official resources for assets, drawing and scene planning. These links open an external site.</p></div><span className="h3-tool-badge">No automatic downloads</span></div>
      <div className="h3-resource-grid">{creativeResources.map(resource => <article key={resource.name}>
        <div className="h3-resource-meta"><span>{resource.kind}</span><span>{resource.license}</span></div>
        <a className="h3-resource-title" href={resource.url} target="_blank" rel="noopener noreferrer">{resource.name}<ExternalLink size={15} /></a>
        <p>{resource.description}</p><small>{resource.note}</small>
        <a className="h3-resource-license" href={resource.licenseUrl} target="_blank" rel="noopener noreferrer">Official license details <ExternalLink size={12} /></a>
      </article>)}</div>
      <p className="h3-tool-note">Import your chosen PNG or JPEG render into the reference library. Asset licenses and software licenses describe different things; the linked official pages explain each resource. Checked 7 September 2026.</p>
    </section>}
    <p className="h3-tools-status" role="status" aria-live="polite">{busy ? 'Preparing PNG…' : status}</p>
  </div>;
}
