/**
 * Demo storyboard used by Remotion Studio (`npm run studio`) and by the renderer's
 * visual self-test. It exercises every scene template with placeholder copy.
 */
import type {SceneData, Timeline} from './shared';

const base = (n: number, template: string, section: string, data: any, extra: Partial<SceneData> = {}): SceneData => ({
  n,
  template,
  data,
  section,
  startFrame: 0,
  durationFrames: 120,
  background: 'grid',
  transition: 'cut',
  status: null,
  source: '',
  cueFrames: [],
  emphasis: null,
  emphasisText: '',
  phrases: [],
  seed: n,
  ...extra,
});

const scenes: SceneData[] = [
  base(1, 'kinetic', 'hook', {lines: ['SAMPLE HOOK', 'GOES HERE'], accent: 'HOOK', kicker: 'Template preview'}, {background: 'rays', transition: 'flash', status: 'reported', emphasis: {from: 20, to: 40}}),
  base(2, 'title', 'hook', {title: 'Episode title card', subtitle: 'One-line framing of the story', date: 'January 1, 2030'}, {background: 'skyline', transition: 'slice'}),
  base(3, 'headline', 'context', {publisher: 'Outlet', date: 'Jan 1, 2030', headline: 'The source article headline appears here with a highlighted phrase', highlight: 'highlighted phrase', byline: 'Reporter Name'}, {background: 'skyline', status: 'reported', source: 'Outlet'}),
  base(4, 'claim', 'story', {claim: 'A key claim from the story is stated here in one sentence.', status: 'confirmed', source: 'Official source', confidence: 'high', note: ''}, {background: 'grid', status: 'confirmed', transition: 'wipe'}),
  base(5, 'stat', 'story', {value: 42, prefix: '', suffix: 'M', decimals: 0, label: 'Sample figure', context: 'One line of context for the number', source: 'Outlet', compare: {value: 12, label: 'Earlier figure', prefix: '', suffix: 'M'}}, {background: 'grid', cueFrames: [26]}),
  base(6, 'bars', 'story', {title: 'Sample comparison', unit: '', source: 'Outlet', items: [{label: 'First item', value: 30, display: '30', highlight: false}, {label: 'Second item', value: 55, display: '55', highlight: false}, {label: 'Highlighted item', value: 90, display: '90', highlight: true}]}, {background: 'dots', cueFrames: [20, 40, 60]}),
  base(7, 'quote', 'evidence', {quote: 'A verified quotation is shown here exactly as it was published.', speaker: 'Speaker Name, Role', publisher: 'Outlet'}, {background: 'dots', status: 'confirmed', transition: 'push'}),
  base(8, 'timeline', 'evidence', {title: 'Sample timeline', events: [{date: 'Jan 2029', label: 'First event', state: 'past'}, {date: 'Jun 2029', label: 'Second event', state: 'past'}, {date: 'Today', label: 'Current event', state: 'now'}, {date: 'Mar 2030', label: 'Upcoming event', state: 'future'}]}, {background: 'dots', cueFrames: [16, 36, 58, 80]}),
  base(9, 'sources', 'evidence', {title: 'Who says what', sources: [{publisher: 'Official', stance: 'confirms', note: 'Published a statement'}, {publisher: 'Outlet A', stance: 'reports', note: 'Cites its own sources'}, {publisher: 'Outlet B', stance: 'disputes', note: 'Reports a different figure'}, {publisher: 'Platform', stance: 'silent', note: 'Has not commented'}]}, {background: 'dots', cueFrames: [14, 34, 54, 74]}),
  base(10, 'bullets', 'analysis', {title: 'Key points', items: ['First point in a few words', 'Second point in a few words', 'Third point in a few words', 'Fourth point in a few words'], style: 'number'}, {background: 'mesh', cueFrames: [14, 34, 54, 74], transition: 'slice'}),
  base(11, 'versus', 'analysis', {title: 'Side by side', left: {title: 'Before', points: ['First trait', 'Second trait'], value: '10'}, right: {title: 'After', points: ['First trait', 'Second trait'], value: '25'}, verdict: 'One-line conclusion goes here'}, {background: 'mesh'}),
  base(12, 'takeaway', 'analysis', {title: 'What it means', cause: 'The news in a few words', effects: ['First consequence', 'Second consequence', 'Third consequence']}, {background: 'mesh', cueFrames: [30, 52, 74]}),
  base(13, 'countdown', 'community', {target_date: '2030-01-20', label: 'Sample milestone', sublabel: 'Optional detail line'}, {background: 'waves', status: 'confirmed', transition: 'glitch'}),
  base(14, 'chapter', 'conclusion', {title: 'THE TAKEAWAY', index: 4, kicker: 'Final section'}, {background: 'grid', durationFrames: 70, transition: 'glitch'}),
  base(15, 'outro', 'cta', {question: 'The closing question for the comments goes here?', cta: 'Tell us below'}, {background: 'rays', durationFrames: 150, transition: 'slice'}),
  // comparisons and top lists (python -m gta_factory topic ...)
  base(16, 'compare', 'item_03', {n: 3, total: 12, title: 'SAMPLE SYSTEM', left: {game: 'OTHER GAME', caption: 'How it used to work'}, right: {game: 'GTA 6', caption: 'How it works now', status: 'reported'}}, {transition: 'slice'}),
  base(17, 'footage', 'item_03', {game: 'GTA 6', side: 'gta6', tag: '03 · SAMPLE SYSTEM'}, {status: 'reported'}),
  base(18, 'item', 'item_04', {n: 4, total: 12, title: 'A sample entry', kicker: 'What it gives you'}, {transition: 'slice', status: 'confirmed'}),
];

let cursor = 0;
for (const s of scenes) {
  s.startFrame = cursor;
  cursor += s.durationFrames;
  const text = 'Captions follow the narration word by word';
  const words = text.split(' ');
  s.phrases = [{s: 8, e: 8 + words.length * 9, words: words.map((w, i) => ({w, s: 8 + i * 9, e: 16 + i * 9}))}];
  s.emphasisText = 'narration';
}

export const SAMPLE_TIMELINE: Timeline = {
  format: 'long',
  fps: 30,
  width: 1920,
  height: 1080,
  totalFrames: cursor,
  title: 'Template preview',
  today: '2030-01-01',
  brand: {name: 'NEON DISPATCH', tagline: 'GTA 6 news, verified.'},
  burnCaptions: true,
  chapters: [
    {title: 'Hook', startFrame: 0},
    {title: 'Context', startFrame: 240},
    {title: 'Story', startFrame: 360},
    {title: 'Evidence', startFrame: 720},
    {title: 'Analysis', startFrame: 1080},
    {title: 'Wrap-up', startFrame: 1440},
  ],
  scenes,
};
