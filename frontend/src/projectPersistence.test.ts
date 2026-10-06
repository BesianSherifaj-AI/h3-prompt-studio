import { describe, expect, it, vi } from 'vitest';
import { ProjectPersistence } from './projectPersistence';

type Draft = { id: string; title: string; idea?: string };
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

describe('local project persistence and switching', () => {
  it('snapshots saves, serializes writes and recovers after a failed save', async () => {
    const first = deferred<unknown>(), writes: Draft[] = [];
    const queue = new ProjectPersistence<Draft>(async project => {
      writes.push(project);
      if (writes.length === 1) return first.promise;
      return { saved: true };
    });
    const original = { id: 'first', title: 'Original' };
    const failed = queue.save(original).catch(error => error);
    original.title = 'Latest edit';
    const next = queue.save(original);
    await tick();
    expect(writes).toEqual([{ id: 'first', title: 'Original' }]);
    expect(queue.isSaved(original)).toBe(false);
    first.reject(new Error('Disk unavailable'));
    expect(await failed).toMatchObject({ message: 'Disk unavailable' });
    await next;
    expect(writes[1].title).toBe('Latest edit');
    expect(queue.isSaved(original)).toBe(true);
  });

  it('flushes edits typed while an earlier save is waiting', async () => {
    const gate = deferred<unknown>(), writes: Draft[] = [];
    let current: Draft = { id: 'old', title: 'Before' };
    const queue = new ProjectPersistence<Draft>(async project => {
      writes.push(project);
      if (writes.length === 1) return gate.promise;
    });
    const saving = queue.flush(() => current);
    await tick();
    current = { ...current, title: 'Typed while saving' };
    gate.resolve({ saved: true });
    await saving;
    expect(writes.map(project => project.title)).toEqual(['Before', 'Typed while saving']);
    expect(queue.isSaved(current)).toBe(true);
  });

  it('does not replace the current project if its save fails', async () => {
    const current = { id: 'old', title: 'My unsaved draft' };
    const queue = new ProjectPersistence<Draft>(async () => { throw new Error('Disk unavailable'); });
    const prepare = vi.fn(async () => ({ id: 'new', title: 'New film' })), activate = vi.fn();
    await expect(queue.transition(() => current, prepare, activate)).rejects.toThrow('Disk unavailable');
    expect(prepare).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
    expect(current.title).toBe('My unsaved draft');
  });

  it('saves outgoing edits during loading and persists the new project before activation', async () => {
    const prepared = deferred<Draft>(), persisted = deferred<unknown>(), writes: Draft[] = [];
    let current: Draft = { id: 'old', title: 'Old project' };
    const queue = new ProjectPersistence<Draft>(async project => {
      writes.push(project);
      if (project.id === 'new' && writes.filter(value => value.id === 'new').length === 1) return persisted.promise;
    });
    const switching = queue.transition(() => current, () => prepared.promise, next => { current = next; });
    await tick();
    current = { ...current, title: 'Edited while loading' };
    prepared.resolve({ id: 'new', title: 'Named film', idea: 'A paper boat drifts.' });
    await tick();
    expect(current.id).toBe('old');
    current = { ...current, idea: 'One last outgoing edit' };
    persisted.resolve({ saved: true });
    await switching;
    expect(writes.filter(project => project.id === 'old').at(-1)?.idea).toBe('One last outgoing edit');
    expect(writes.at(-1)?.id).toBe('new');
    expect(current).toEqual({ id: 'new', title: 'Named film', idea: 'A paper boat drifts.' });
    expect(queue.isSaved(current)).toBe(true);
  });

  it('rejects overlapping opens and prevents stale load responses from replacing another project', async () => {
    const prepared = deferred<Draft>();
    let current: Draft = { id: 'old', title: 'Old' };
    const queue = new ProjectPersistence<Draft>(async () => {}), activate = vi.fn();
    const first = queue.transition(() => current, () => prepared.promise, activate);
    await expect(queue.transition(() => current, async () => ({ id: 'second', title: 'Second' }), activate)).rejects.toThrow('Another project');
    await tick();
    current = { id: 'other', title: 'Current project' };
    prepared.resolve({ id: 'stale', title: 'Stale response' });
    await expect(first).rejects.toThrow('older opening request');
    expect(activate).not.toHaveBeenCalled();
    await queue.transition(() => current, async () => ({ id: 'fresh', title: 'Fresh' }), next => { current = next; });
    expect(current.id).toBe('fresh');
  });

  it('remembers deliberate loads without rewriting their saved document', async () => {
    let current = { id: 'old', title: 'Old' };
    const write = vi.fn(async (_project:Draft) => {}), remember = vi.fn(async (_project:Draft) => ({}));
    const queue = new ProjectPersistence<Draft>(write);
    await queue.transition(() => current, async () => ({ id: 'saved', title: 'Saved film' }), next => { current = next; }, undefined, remember);
    expect(write.mock.calls.every(([project]) => project.id === 'old')).toBe(true);
    expect(remember).toHaveBeenCalledWith({ id: 'saved', title: 'Saved film' });
    expect(queue.isSaved(current)).toBe(true);
  });
});
