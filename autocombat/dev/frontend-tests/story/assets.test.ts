import {it,expect} from 'vitest';
import {referenceForAsset} from '../../../frontend/src/story/assets';
it('inserts typed references while preserving filenames',()=>{
  expect(referenceForAsset('background/bg.svg')).toBe('\\bg bg.svg');expect(referenceForAsset('bgm/long name.wav')).toBe('\\m long name.wav');expect(referenceForAsset('figure/a.svg')).toBe('changeFigure:a.svg;');expect(referenceForAsset('vocal/chime.wav')).toBe('\\se chime.wav');expect(referenceForAsset('video/opening.webm',true)).toBe('->opening.webm');expect(referenceForAsset('opening.mp4')).toBe('\\video opening.mp4');expect(()=>referenceForAsset('unknown.bin')).toThrow();
});
