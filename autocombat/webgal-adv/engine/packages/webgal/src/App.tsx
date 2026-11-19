import { useEffect } from 'react';
import { initializeScript } from '@/Core/initializeScript';
import Translation from '@/UI/Translation/Translation';
import { Stage } from '@/Stage/Stage';
import { BottomControlPanel } from '@/UI/BottomControlPanel/BottomControlPanel';
import { BottomControlPanelFilm } from '@/UI/BottomControlPanel/BottomControlPanelFilm';
import { Backlog } from '@/UI/Backlog/Backlog';
import Title from '@/UI/Title/Title';
import Logo from '@/UI/Logo/Logo';
import { Extra } from '@/UI/Extra/Extra';
import Menu from '@/UI/Menu/Menu';
import GlobalDialog from '@/UI/GlobalDialog/GlobalDialog';
import PanicOverlay from '@/UI/PanicOverlay/PanicOverlay';
import DevPanel from '@/UI/DevPanel/DevPanel';
import { hosted } from '@/Core/hosted/context';
import { HostedPanel } from '@/UI/Hosted/HostedPanel';

export default function App() {
  useEffect(() => {
    initializeScript();
  }, []);
  return (
    <div className="App">
      {!hosted && <Translation />}
      <Stage />
      {!hosted && <BottomControlPanel />}
      {!hosted && <BottomControlPanelFilm />}
      {hosted && <HostedPanel />}
      <Backlog />
      {!hosted && <Title />}
      {!hosted && <Logo />}
      <Extra />
      <Menu />
      <GlobalDialog />
      <PanicOverlay />
      <DevPanel />
    </div>
  );
}
