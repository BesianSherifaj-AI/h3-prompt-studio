/** Serialize local saves and preserve edits typed while another project opens. */
export class ProjectPersistence<T extends { id: string }> {
  private tail: Promise<unknown> = Promise.resolve();
  private transitioning = false;
  private savedDrafts = new Map<string, string>();

  constructor(private readonly write: (project: T) => Promise<unknown>) {}

  acknowledge(project: T) {
    this.savedDrafts.set(project.id, JSON.stringify(project));
  }

  isSaved(project: T | null): boolean {
    return !project || this.savedDrafts.get(project.id) === JSON.stringify(project);
  }

  save(project: T): Promise<unknown> {
    const snapshot = structuredClone(project);
    const next = this.tail.catch(() => {}).then(async () => {
      const result = await this.write(snapshot);
      this.acknowledge(snapshot);
      return result;
    });
    this.tail = next;
    return next;
  }

  async flush(read: () => T | null): Promise<unknown> {
    const originalId = read()?.id;
    let result: unknown;
    while (read()) {
      const project = read()!;
      if (project.id !== originalId) throw new Error('The active project changed while saving. Open the intended project again.');
      const draft = JSON.stringify(project);
      result = await this.save(project);
      const current = read();
      if (current?.id !== originalId) throw new Error('The active project changed while saving. Open the intended project again.');
      if (JSON.stringify(current) === draft) return result;
    }
    return result;
  }

  async transition(
    read: () => T | null,
    prepare: () => Promise<T>,
    activate: (project: T) => void,
    flush: () => Promise<unknown> = () => this.flush(read),
    persist: (project: T) => Promise<unknown> = project => this.save(project),
  ): Promise<T> {
    if (this.transitioning) throw new Error('Another project is opening. Wait for it to finish.');
    this.transitioning = true;
    const originalId = read()?.id;
    const assertCurrent = () => {
      if (read()?.id !== originalId) throw new Error('The active project changed. The older opening request was not applied.');
    };
    try {
      await flush();
      assertCurrent();
      const next = await prepare();
      assertCurrent();
      while (true) {
        await flush();
        assertCurrent();
        const outgoing = JSON.stringify(read());
        await persist(next);
        assertCurrent();
        if (JSON.stringify(read()) === outgoing) break;
      }
      this.acknowledge(next);
      activate(next);
      return next;
    } finally {
      this.transitioning = false;
    }
  }
}
