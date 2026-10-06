import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import WorkspaceNavigation from './WorkspaceNavigation';

describe('three distinct workspaces',()=>{
  it('shows explicit destinations, durations, utilities and current page',()=>{
    const html=renderToStaticMarkup(createElement(WorkspaceNavigation,{mode:'video',onNavigate:()=>{},onConnections:()=>{},onHelp:()=>{}}));
    for(const destination of ['video','studio','game'])expect(html).toContain(`href="/${destination}"`);
    expect(html).toContain('One clip · up to 15s');
    expect(html).toContain('Connected films · 1–10 min');
    expect(html).toContain('href="/video" aria-current="page"');
    expect(html).toContain('href="#video-workspace"');
    expect(html).toContain('Connections');expect(html).toContain('Help');
  });
});
