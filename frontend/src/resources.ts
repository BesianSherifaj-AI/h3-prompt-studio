export type CreativeResource = {
  name: string; kind: 'Assets' | 'Software'; url: string; licenseUrl: string;
  license: string; description: string; note: string;
};

/** Official resource and license pages checked 2026-09-07. Links only; no external assets are embedded. */
export const creativeResources: CreativeResource[] = [
  {
    name: 'Poly Haven', kind: 'Assets', url: 'https://polyhaven.com/',
    licenseUrl: 'https://polyhaven.com/license', license: 'CC0 assets',
    description: 'HDRI lighting, textures and 3D models for staging a reference scene.',
    note: 'Downloaded assets allow commercial use without attribution. Website content and API access have separate terms.',
  },
  {
    name: 'Kenney', kind: 'Assets', url: 'https://kenney.nl/assets',
    licenseUrl: 'https://kenney.nl/support', license: 'CC0 asset packs',
    description: 'Simple props, characters and environments for quick visual layouts.',
    note: 'Assets on the asset pages are CC0; check the included license. Kenney software and branding are separate.',
  },
  {
    name: 'ambientCG', kind: 'Assets', url: 'https://ambientcg.com/',
    licenseUrl: 'https://docs.ambientcg.com/license/', license: 'CC0 assets',
    description: 'Materials, models and lighting resources for surfaces and atmosphere.',
    note: 'Downloadable assets and material preview renders are CC0. Commercial use is allowed; credit is optional.',
  },
  {
    name: 'Blender', kind: 'Software', url: 'https://www.blender.org/',
    licenseUrl: 'https://www.blender.org/about/license/', license: 'Free · GNU GPL',
    description: 'Build a real 3D scene, animate a camera and render more precise guide images.',
    note: 'The application is GPL software. Your original renders and artwork do not become GPL because you use Blender.',
  },
  {
    name: 'Krita', kind: 'Software', url: 'https://krita.org/en/',
    licenseUrl: 'https://krita.org/en/about/license/', license: 'Free · GPL v3',
    description: 'Draw richer reference images and organize shots in the Storyboard docker.',
    note: 'Free on the official site; store editions may cost money. Your original artwork remains yours, including commercial work.',
  },
];
