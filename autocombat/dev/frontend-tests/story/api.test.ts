import {it,expect,vi,afterEach} from 'vitest';
import {storyApi} from '../../../frontend/src/story/api';
afterEach(()=>vi.unstubAllGlobals());
it('uses GET for reads and JSON POST for mutations',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({value:3})});vi.stubGlobal('fetch',fetch);
  expect(await storyApi('/projects')).toEqual({value:3});expect(fetch.mock.calls[0]).toEqual(['/api/ui/story/projects',{}]);
  await storyApi('/runs',{entry:'START'});expect(fetch.mock.calls[1][1].method).toBe('POST');expect(fetch.mock.calls[1][1].body).toContain('START');
});
it('preserves error codes and diagnostics',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:409,json:async()=>({message:'conflict',diagnostics:[{line:2}]})}));
  await expect(storyApi('/x')).rejects.toMatchObject({message:'conflict',status:409,diagnostics:[{line:2}]});
});
