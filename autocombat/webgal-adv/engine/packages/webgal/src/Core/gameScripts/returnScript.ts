import { ISentence } from '@/Core/controller/scene/sceneInterface';
import { createNonePerform, IPerform } from '@/Core/Modules/perform/performInterface';
import { returnFromScene } from '@/Core/controller/scene/returnFromScene';
import { resolveSetVarValue } from './setVar';
import { hosted } from '@/Core/hosted/context';
import { storyHost } from './storyHost';
import { WebGAL } from '@/Core/WebGAL';

/**
 * 从被调用的场景返回，可携带返回值。返回值在被调用场景的作用域内求值。
 * @param sentence
 */
export const returnScript = (sentence: ISentence): IPerform => {
  if (hosted && WebGAL.sceneManager.sceneData.sceneStack.length === 0) return storyHost();
  // 不写冒号时（`return;`）解析器会把整条命令留在 content 里，此时视为无返回值
  const valExp = sentence.content === sentence.commandRaw ? '' : sentence.content;
  returnFromScene(resolveSetVarValue(valExp));
  return createNonePerform({ isHoldOn: true });
};
