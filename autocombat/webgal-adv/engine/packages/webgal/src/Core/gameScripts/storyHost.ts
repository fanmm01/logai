import { IPerform } from '@/Core/Modules/perform/performInterface';
import { completeHostedBlock } from '@/Core/hosted/runtime';
export function storyHost(): IPerform {
  return { performName: 'story-host', duration: 86400000, isHoldOn: true,
    startFunction: () => { setTimeout(completeHostedBlock, 0); }, stopFunction: () => {},
    blockingNext: () => true, blockingAuto: () => true, blockingStateCalculation: () => true };
}
