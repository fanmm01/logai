/** Pass unused vertical wheel movement to the other editor pane. */
export function linkPaneScrolling(layout: HTMLElement) {
  function onWheel(event: WheelEvent) {
    if (event.defaultPrevented || !event.cancelable || event.ctrlKey || event.shiftKey ||
        !event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    if (!(event.target instanceof Element)) return;
    const pane = event.target.closest<HTMLElement>('.editor-roster, .editor-content');
    if (!pane || pane.parentElement !== layout) return;
    const other = Array.from(layout.children).find(child => child !== pane) as HTMLElement | undefined;
    if (!other) return;

    const lineHeight = parseFloat(getComputedStyle(pane).lineHeight) || 16;
    const delta = event.deltaY * (event.deltaMode === 1 ? lineHeight : event.deltaMode === 2 ? pane.clientHeight : 1);
    const next = Math.max(0, Math.min(pane.scrollTop + delta, pane.scrollHeight - pane.clientHeight));
    const remaining = delta - (next - pane.scrollTop);
    if (!remaining) return;
    const otherNext = Math.max(0, Math.min(other.scrollTop + remaining, other.scrollHeight - other.clientHeight));
    if (otherNext === other.scrollTop) return;

    // Leave nested scrollable controls to the browser while they can still scroll.
    for (let node: Element | null = event.target; node && node !== pane; node = node.parentElement) {
      if (!(node instanceof HTMLElement)) continue;
      const overflow = getComputedStyle(node).overflowY;
      if (!['auto', 'scroll'].includes(overflow)) continue;
      if (delta > 0 ? node.scrollTop < node.scrollHeight - node.clientHeight : node.scrollTop > 0) return;
    }

    event.preventDefault();
    pane.scrollTop = next;
    other.scrollTop = otherNext;
  }

  layout.addEventListener('wheel', onWheel, { passive: false });
  return () => layout.removeEventListener('wheel', onWheel);
}
