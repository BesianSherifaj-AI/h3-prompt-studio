import { useEffect, useRef, useState } from 'react';
import type { Project } from './model';
import { continuationReviewDefaults, type ContinuationLink, type ContinuationReviewValues } from './continuationLink';
import './ContinuationLinkReview.css';

type Props = {
  link: ContinuationLink | null; source: Project | null; loading: boolean; error: string; busy: boolean;
  onClose: () => void; onRetry: () => void; onCreate: (values: ContinuationReviewValues) => void;
};

export default function ContinuationLinkReview({ link, source, loading, error, busy, onClose, onRetry, onCreate }: Props) {
  const panel = useRef<HTMLElement>(null);
  const firstInput = useRef<HTMLTextAreaElement>(null);
  const [values, setValues] = useState<ContinuationReviewValues>({ request: '', ending: '', duration: 15 });
  useEffect(() => {
    if (source) { setValues(continuationReviewDefaults(source)); firstInput.current?.focus(); }
  }, [source?.id]);
  useEffect(() => { panel.current?.focus(); }, []);
  const ready = !!source && !!link && !loading && !error;
  const frames = Math.ceil((values.duration * 24 - 5) / 17) * 17 + 5;
  const durations = [...new Set([5, 7, 10, 15, values.duration])].sort((a, b) => a - b);
  return <div className="modal-backdrop continuation-link-backdrop">
    <section ref={panel} tabIndex={-1} className="modal continuation-link-review" role="dialog" aria-modal="true" aria-labelledby="continuation-link-title"
      onKeyDown={event => {
        if (event.key === 'Escape' && !busy) onClose();
        if (event.key !== 'Tab') return;
        const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled), select:not(:disabled)') ?? []);
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      <header><div><h2 id="continuation-link-title">Continue this saved video</h2><p>Review the next action, then create a separate clip.</p></div>
        <button type="button" className="icon-button" aria-label="Close continuation review" disabled={busy} onClick={onClose}>×</button></header>
      <div className="continuation-link-body">
        {link && <p className="continuation-link-file"><strong>Saved motion and audio</strong><code>{link.source}</code></p>}
        {loading && <p role="status">Checking the source project and exact saved clip…</p>}
        {error && <div className="continuation-link-error" role="alert"><p>{error}</p><p>Your current draft stays open. A new video will not be substituted.</p>{link && <button type="button" disabled={loading || busy} onClick={onRetry}>Check again</button>}</div>}
        {source && <p>Based on <strong>{source.title || 'Untitled project'}</strong>. The people, clothes, reference tags and render settings carry over; new dialogue starts empty.</p>}
        {ready && <>
          <label className="field"><span>What happens next?</span><textarea ref={firstInput} aria-label="Continuation next idea" rows={3} value={values.request} disabled={busy}
            placeholder="For example: she turns toward the window, still holding the box, and starts walking."
            onChange={event => setValues({ ...values, request: event.target.value })} /></label>
          <label className="field"><span>Where did the saved clip finish?</span><textarea aria-label="Continuation previous ending" rows={2} value={values.ending} disabled={busy}
            placeholder="Describe the actual final pose, camera position and who holds each object."
            onChange={event => setValues({ ...values, ending: event.target.value })} /></label>
          <label className="field"><span>Requested clip length · includes carried motion</span><select aria-label="Continuation requested length" value={values.duration} disabled={busy}
            onChange={event => setValues({ ...values, duration: Number(event.target.value) })}>
            {durations.map(n => <option value={n} key={n}>{n} seconds</option>)}</select></label>
          <p className="continuation-link-timing">{(frames / 24).toFixed(3)}s generated = 1.625s carried motion + {((frames - 39) / 24).toFixed(3)}s new footage. The saved clip supplies its original resolution.</p>
          {link.seed !== undefined && <p className="continuation-link-timing">Next seed: {link.seed}</p>}
          <p className="continuation-link-timing">These settings come from the saved Studio project. Review any LoRA or sampling changes you made directly in ComfyUI before sending the next workflow.</p>
        </>}
        <p className="continuation-link-timing">Your current draft and the source project remain saved separately. Creating the clip does not call the AI or start a render.</p>
      </div>
      <footer><button type="button" disabled={busy} onClick={onClose}>Keep current draft</button>
        <button type="button" className="primary" disabled={!ready || busy} onClick={() => onCreate(values)}>{busy ? 'Saving separate clip…' : 'Create continuation'}</button></footer>
    </section>
  </div>;
}
