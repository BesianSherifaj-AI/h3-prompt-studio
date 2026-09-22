import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ProductionQueue, { ProductionBatchView, productionActions, productionExportOptions, productionProgress, productionStatus, productionTrimEdits, type ProductionBatch } from './ProductionQueue';

const fixture: ProductionBatch = { id: 'batch-one', name: 'Six before sunrise', status: 'needs_attention', completed: 1, total: 3, items: [
  { index: 0, project_id: 'p1', title: 'Opening', status: 'succeeded', video_url: '/api/video/runs/r1/video', duration: 8 },
  { index: 1, project_id: 'p2', title: 'The signal', status: 'failed', error: 'ComfyUI went offline.' },
  { index: 2, project_id: 'p3', title: 'Finale', status: 'queued' },
] };
const noop = () => {};
function render(batch = fixture, pending = false) {
  return renderToStaticMarkup(<ProductionBatchView batch={batch} pending={pending} onAction={noop} onRetry={noop} onPreview={noop} onOpenProject={noop}/>);
}
describe('Production queue progress and recovery', () => {
  it('counts successful renders separately from total planned work', () => {
    expect(productionProgress(fixture)).toEqual({ total: 3, completed: 1, percent: 33 });
    expect(productionProgress({ ...fixture, total: 0, items: [], completed: 5 })).toEqual({ total: 0, completed: 0, percent: 0 });
  });
  it('offers only appropriate batch controls for persisted state', () => {
    expect(productionActions('draft')).toEqual({ start: true, resume: false, cancel: false });
    expect(productionActions('running')).toEqual({ start: false, resume: false, cancel: true });
    for (const status of ['paused', 'needs_attention']) expect(productionActions(status).resume).toBe(true);
    for (const status of ['succeeded', 'cancelled']) expect(Object.values(productionActions(status))).toEqual([false, false, false]);
  });
  it('retains failure details and requires deliberate retry, then resume', () => {
    const html = render();
    expect(html).toContain('ComfyUI went offline.');
    expect(html).toContain('Resume remaining');
    expect(html).toContain('>Retry</button>');
    expect(html).not.toContain('Start batch');
    expect(html).toContain('1/3 rendered');
  });
  it('does not retry uncertain submissions or offer retry while another item runs', () => {
    const uncertain = render({ ...fixture, items: [{ ...fixture.items[1], status: 'uncertain' }] });
    expect(uncertain).toContain('Checking submission');
    expect(uncertain).not.toContain('>Retry</button>');
    const running = render({ ...fixture, status: 'running' });
    expect(running).toContain('Stop queue');
    expect(running).toContain('disabled="">Retry');
    expect(running).not.toContain('Resume remaining');
  });
  it('offers review for rendered media without claiming accepted story state', () => {
    const html = render({ ...fixture, status: 'succeeded' });
    expect(html).toContain('Play Opening');
    expect(html).toContain('/api/production/batch-one/playlist');
    expect(html).toContain('does not accept Game actions');
    expect(productionStatus('succeeded')).toBe('Rendered · review takes');
  });
  it('keeps creation and saved project selection inside Studio', () => {
    const html = renderToStaticMarkup(<ProductionQueue active currentProjectId="p1" currentProjectTitle="Opening" onSaveCurrent={async () => {}} onOpenProject={async () => {}}/>);
    expect(html).toContain('Production queue');
    expect(html).toContain('Queue current project');
    expect(html).toContain('Create batch · 0 selected');
    expect(html).toContain('one video at a time');
  });
  it('offers film and clip exports only after all planned items succeeded', () => {
    const props = { pending: false, onAction: noop, onRetry: noop, onPreview: noop, onOpenProject: noop, onExport: noop };
    const done = renderToStaticMarkup(<ProductionBatchView {...props} batch={{ ...fixture, status: 'succeeded', completed: 3 }}/>);
    expect(done).toContain('Export film'); expect(done).toContain('Export clips ZIP');
    const incomplete = renderToStaticMarkup(<ProductionBatchView {...props} batch={fixture}/>);
    expect(incomplete).not.toContain('Export film');
  });
  it('links generated first frames while video is queued without preloading images', () => {
    const html = render({ ...fixture, items: [{ ...fixture.items[2], asset_url: '/api/assets/frame/file', run_id: 'waiting-run' }] });
    expect(html).toContain('aria-label="First frame for Finale"');
    expect(html).toContain('href="/api/assets/frame/file" target="_blank"');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('/api/video/runs/waiting-run/project');
  });
  it('provides the exact rendered source snapshot only for finished videos', () => {
    const html = render({ ...fixture, items: [{ ...fixture.items[0], run_id: 'rendered-run' }] });
    expect(html).toContain('href="/api/video/runs/rendered-run/project" download=""');
    expect(html).toContain('aria-label="Download source project for Opening"');
    expect(html).not.toContain('First frame');
  });
  it('shows the persisted latest export independently of the browser session', () => {
    const html = render({ ...fixture, latest_export: { url: '/api/production/batch-one/exports/crop-final', filename: 'sunrise-cropped.mp4', kind: 'film', export_id: 'crop-final', clip_count: 18 } });
    expect(html).toContain('href="/api/production/batch-one/exports/crop-final" download="sunrise-cropped.mp4"');
    expect(html).toContain('aria-label="Download latest export: sunrise-cropped.mp4"');
    expect(html).toContain('Latest saved film export · 18 clips');
    expect(render()).not.toContain('Download latest export');
  });
  it('balances clip ZIP audio when selected without changing film audio', () => {
    expect(productionExportOptions('clips', true)).toEqual({ kind: 'clips', normalize_audio: true });
    expect(productionExportOptions('clips', false)).toEqual({ kind: 'clips', normalize_audio: false });
    expect(productionExportOptions('film', true)).toEqual({ kind: 'film', normalize_audio: false });
    const props = { batch: { ...fixture, status: 'succeeded', completed: 3 }, onAction: noop, onRetry: noop, onPreview: noop, onOpenProject: noop, onExport: noop, onBalanceAudioChange: noop };
    const html = renderToStaticMarkup(<ProductionBatchView {...props}/>);
    expect(html).toContain('type="checkbox" checked=""/>Balance clip volume');
    expect(html).toContain('Matches quiet and loud clips in the ZIP.');
    expect(renderToStaticMarkup(<ProductionBatchView {...props} balanceAudio={false}/>)).not.toContain('checked=""');
  });
  it('labels saved exports using their actual audio settings', () => {
    const saved = { url: '/exports/clips.zip', filename: 'clips.zip', kind: 'clips' as const, export_id: 'audio-export', clip_count: 3 };
    expect(render({ ...fixture, latest_export: { ...saved, normalize_audio: true } })).toContain('Balanced audio');
    expect(render({ ...fixture, latest_export: saved })).not.toContain('Balanced audio');
  });
  it('frame-aligns timing while retaining saved crops and untouched item edits', () => {
    const crop = { index: 0, cut_at: 2, crop: { x: 0, y: 0, width: 100, height: 100 } };
    const untouched = { index: 1, in_point: 1, out_point: 3 };
    const edits = productionTrimEdits(fixture.items, { 0: { in_point: '1.01', out_point: '3.99' } }, [crop, untouched]);
    expect(edits).toEqual([{ ...crop, in_point: 1, out_point: 4 }, untouched]);
    expect(productionExportOptions('film', false, edits)).toEqual({ kind: 'film', normalize_audio: false, edits });
    expect(productionTrimEdits(fixture.items, { 0: { in_point: '0', out_point: '8' } }, edits)).toEqual([crop, untouched]);
  });
  it('rejects blank, inverted, out-of-range and invisible crop timing', () => {
    for (const [start, end] of [['', '4'], ['1', '1'], ['4', '2'], ['-1', '4'], ['0', '9'], ['NaN', '4'], ['1', '1.001']]) {
      expect(() => productionTrimEdits(fixture.items, { 0: { in_point: start, out_point: end } })).toThrow();
    }
    expect(() => productionTrimEdits(fixture.items, { 0: { in_point: '0', out_point: '2' } }, [
      { index: 0, cut_at: 2, crop: { x: 0, y: 0, width: 32, height: 32 } },
    ])).toThrow('saved crop cut');
  });
  it('restores saved trim controls and explains source-time audio synchronization', () => {
    const batch: ProductionBatch = { ...fixture, status: 'succeeded', completed: 1, total: 1, items: [fixture.items[0]],
      latest_export: { url: '/film', filename: 'film.mp4', kind: 'film', export_id: 'trimmed', clip_count: 1,
        duration: 2.5, edits: [{ index: 0, in_point: 1, out_point: 3.5 }] } };
    const html = renderToStaticMarkup(<ProductionBatchView batch={batch} onAction={noop} onRetry={noop} onPreview={noop} onOpenProject={noop} onExport={noop}/>);
    expect(html).toContain('Trim timing · 2.50s film');
    expect(html).toContain('aria-label="In point for Opening"');
    expect(html).toContain('value="3.5"');
    expect(html).toContain('audio follows the same range');
    expect(html).toContain('Cuts snap to 24 fps');
    expect(html).toContain('Reset timing');
  });
});
