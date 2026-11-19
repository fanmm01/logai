export const pages = ['home', 'setup', 'battle', 'sim', 'library', 'editor', 'tournament', 'settings', 'help/mechanics', 'help/characters', 'story', 'story/play', 'story/editor'] as const;
export function pageFromPath(path: string) {
  const page = path.replace(/^\/ui\/?/, '').replace(/\/$/, '');
  return (pages as readonly string[]).includes(page) ? page : 'home';
}
export function pageUrl(page: string, search = '') { return '/ui/' + page + search; }
