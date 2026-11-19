import { useEffect, useState } from 'react';
import { hosted } from '@/Core/hosted/context';

const fullscreenDocument = hosted ? window.parent.document : document;
const _isFullScreen = () => !!(fullscreenDocument.fullscreenElement || (fullscreenDocument as any).webkitFullscreenElement);
export default function useFullScreen(target: Element = fullscreenDocument.querySelector('.story-player-shell') || fullscreenDocument.documentElement): {
  isSupported: boolean;
  isFullScreen: boolean;
  enter: () => Promise<void>;
  exit: () => Promise<void>;
  toggle: () => Promise<void>;
} {
  const isSupported = fullscreenDocument.fullscreenEnabled || (fullscreenDocument as any).webkitFullscreenEnabled;
  const [isFullScreen, setFullScreen] = useState<boolean>(_isFullScreen());

  useEffect(() => {
    if (!isSupported) return;
    const onFullscreenChange = () => setFullScreen(_isFullScreen());

    fullscreenDocument.addEventListener('fullscreenchange', onFullscreenChange);

    return () => {
      fullscreenDocument.removeEventListener('fullscreenchange', onFullscreenChange);
    };
  }, []);

  const enter = isSupported ? () => target.requestFullscreen() : async () => {};
  const exit = isSupported ? () => fullscreenDocument.exitFullscreen() : async () => {};

  return {
    isSupported,
    isFullScreen: isFullScreen,
    enter,
    exit,
    toggle: () => (isFullScreen ? exit() : enter()),
  };
}
