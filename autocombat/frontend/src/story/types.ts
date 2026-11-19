export type Entry = {id:string;kind:'start'|'continue';label:string};
export type Chapter = {path:string;title:string;description:string;unlocked:boolean;reason:string;completed:boolean;entries:Entry[]};
export type Project = {id:string;name:string;version:string;workspace:string;chapters:Chapter[]};
export type StoryScene = {token:string;path:string;script:string;source_map:{path:string;line:number}[]};
export type StoryRun = {id:string;project:string;version:string;flow:string;status:string;revision:number;message:string;scene:StoryScene|null;player_snapshot:any;user_data:any;room:string|null;last_result?:string;choice?:{options:{id:string;text:string;enabled:boolean}[]}|null;encounter?:{teams:Record<string,string[]>;battles:{a:string;b:string;keep:boolean;carry:Record<string,boolean>}[];bind:string;wave:number;control:string};successors?:Chapter[];slots:{id:string;slot:string;flow:string;time:number}[]};
export type Graph = {nodes:{id:string;kind:string;label:string;line:number;start?:number;end?:number;index:number;targetPath?:string}[];edges:{id:string;source:string;target:string;label:string}[];entries:any[]};
export type Workspace = {id:string;path:string;name:string};
export type SourceFile = {path:string;content:string;hash:string|null};
