import { useId } from 'react';
import { Camera, Check, Sparkles } from 'lucide-react';
import type { Project } from './model';
import './PromptWorkbench.css';

export type PromptDirection = { opening: string; action: string; ending: string; camera: string; preserve: string };
const EMPTY: PromptDirection = { opening: '', action: '', ending: '', camera: 'Static camera, eye level, medium shot', preserve: '' };
const PRESETS: Record<string, Partial<PromptDirection>> = {
  'Character moment': { opening: 'The character is still, clearly visible in the established location.', action: 'One subtle expression followed by one deliberate gesture.', ending: 'The character settles into a clear, steady final pose.', preserve: 'Keep the same face, wardrobe, lighting and background throughout.' },
  'Product showcase': { opening: 'The product is fully visible and held steady.', action: 'One slow turn reveals the product details.', ending: 'The product faces the camera and remains still.', preserve: 'Preserve the product shape, materials, proportions and existing markings.' },
  'Prop handoff': { opening: 'One person holds one prop; the receiver has empty hands.', action: 'The holder extends the prop. The receiver grasps it, then the holder releases it.', ending: 'Only the receiver holds the same single prop; both hands settle.', preserve: 'Keep one instance of the prop. Preserve both identities and outfits; keep bystanders still.' },
};

export function directionText(direction: PromptDirection): string {
  return [['Opening', direction.opening], ['Action', direction.action], ['Ending', direction.ending],
    ['Camera', direction.camera], ['Continuity', direction.preserve]]
    .filter(([, text]) => text.trim()).map(([label, text]) => `${label}: ${text.trim()}`).join('\n');
}

export function applyPromptDirection(project: Project, direction: PromptDirection): void {
  const text = directionText(direction);
  const previous = project.prompt_workbench_applied;
  if (typeof previous === 'string' && previous && project.story.text.includes(previous)) {
    project.story.text = project.story.text.replace(previous, text);
  } else if (!project.story.text.includes(text)) {
    project.story.text = [project.story.text.trim(), text].filter(Boolean).join('\n\n');
  }
  project.prompt_workbench_applied = text;
  project.story.locked = true;
}

/** These are writing checks, not a prediction of generated-video quality. */
export function promptReadiness(project: Project): { label: string; ready: boolean; hint: string }[] {
  const brief = project.story.text.trim();
  const direction = project.prompt_workbench || {};
  const dialogue = project.shots.flatMap(shot => shot.dialogue || []).map(line => String(line.text || '')).join(' ');
  const words = dialogue.trim() ? dialogue.trim().split(/\s+/).length : 0;
  const ending = !!direction.ending?.trim() || project.shots.some(shot => shot.final_state?.trim()) || /\b(end(?:s|ing)?|finish(?:es)?|finally|settles)\b/i.test(brief);
  return [
    { label: 'Clear action', ready: !!brief, hint: 'Describe one visible action, who performs it, and where.' },
    { label: 'Defined ending', ready: ending, hint: 'State the final pose or prop position so the next shot can continue.' },
    { label: 'Manageable scene timing', ready: project.shots.every(shot => shot.duration >= 2), hint: 'Give each scene at least two seconds or simplify the action.' },
    { label: 'Speakable dialogue', ready: words <= project.duration * 2.5, hint: 'Shorten spoken lines or extend the clip; this draft has more than 2.5 words per second.' },
  ];
}

export default function PromptWorkbench({ project, update }: { project: Project; update: (fn: (draft: Project) => void) => void }) {
  const id = useId();
  const direction: PromptDirection = { ...EMPTY, ...project.prompt_workbench };
  const checks = promptReadiness(project);
  const edit = (key: keyof PromptDirection, value: string) => update(draft => {
    draft.prompt_workbench = { ...EMPTY, ...draft.prompt_workbench, [key]: value };
  });
  return <details className="prompt-workbench">
    <summary><Camera size={16} /> Guided prompt writer <span>Opening → action → ending</span></summary>
    <p>Build a precise scene, then add it to your idea. Your photos, existing text and exact dialogue stay connected.</p>
    <div className="prompt-presets" aria-label="Prompt starting points">
      {Object.keys(PRESETS).map(name => <button type="button" key={name} onClick={() => update(draft => {
        draft.prompt_workbench = { ...direction, ...PRESETS[name] };
      })}>{name}</button>)}
    </div>
    <div className="prompt-direction-grid">
      {([['opening', 'Opening state', 'Who is where, and who holds each prop?'], ['action', 'One main action', 'Describe visible steps in their physical order.'],
        ['ending', 'Ending state', 'Final pose, location and object ownership.'], ['camera', 'Camera direction', 'Framing, movement and focus.'],
        ['preserve', 'Keep consistent', 'Faces, clothing, objects, background and lighting.']] as const).map(([key, label, hint]) =>
        <label key={key} htmlFor={`${id}-${key}`}><span>{label}</span><textarea id={`${id}-${key}`} rows={2} value={direction[key]} placeholder={hint} onChange={event => edit(key, event.target.value)} /></label>)}
    </div>
    <button type="button" className="prompt-apply" disabled={!direction.action.trim()} onClick={() => update(draft => {
      applyPromptDirection(draft, direction);
    })}><Sparkles size={15} /> Add direction to my idea</button>
    <div className="prompt-writing-checks" aria-label="Prompt writing checks">
      {checks.map(check => <p key={check.label} className={check.ready ? 'is-ready' : ''}>
        {check.ready ? <Check size={14} /> : <span aria-hidden="true">○</span>} <strong>{check.label}</strong>
        {!check.ready && <small>{check.hint}</small>}
      </p>)}
    </div>
    <small>These checks help you write. Watch the rendered video to judge its quality.</small>
  </details>;
}
