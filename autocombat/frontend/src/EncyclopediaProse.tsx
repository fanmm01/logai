import { Fragment } from 'react';
import { TermText } from './KnowledgeUI';
import { resolveEncyclopediaValues, type EncyclopediaData } from './encyclopediaValues';
import './encyclopedia.css';

// Older articles store section titles as plain array entries.
const sectionTitles = new Set(['施放要求', '技能效果', '冰冻来源', '八层循环', '叠加与衰减', '小雪人']);

function Inline({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}><TermText text={part.slice(2, -2)}/></strong>
      : part.startsWith('`') && part.endsWith('`') ? <code key={i}><TermText text={part.slice(1, -1)}/></code>
        : <Fragment key={i}><TermText text={part}/></Fragment>)}</>;
}

function RuleText({ text }: { text: string }) {
  const label = text.match(/^([^：:。！？]{1,12})[：:]\s*(.+)$/);
  return label ? <><strong className="prose-label"><Inline text={label[1]}/>：</strong><Inline text={label[2]}/></> : <Inline text={text}/>;
}

function parameter(text: string) {
  // Accept both existing “属性：数值” entries and Markdown “**属性**数值”.
  const match = text.match(/^\*\*([^*\n]{1,12})\*\*\s*[：:]?\s*(.+)$/)
    ?? text.match(/^([^：:。！？*#>]{1,12})[：:]\s*(.+)$/);
  if (!match || /^第\s*\d+\s*层$/.test(match[1]) || /^[-+]|^\d+[.)、]/.test(match[1])) return null;
  // Explanatory prose stays in paragraphs, even when it begins with a label.
  if (match[2].length > 80 || /[。！？].+/.test(match[2])) return null;
  return { label: match[1], value: match[2] };
}

export function EncyclopediaProse({ content, sourceKey, data }: { content: string | string[]; sourceKey?:string; data?: EncyclopediaData }) {
  const lines = (Array.isArray(content) ? content : [content]).map(text => data ? resolveEncyclopediaValues(text, data) : text).flatMap(text => text.split(/\r?\n/));
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading || sectionTitles.has(line)) {
      blocks.push(<h5 key={i}><Inline text={heading?.[2] ?? line}/></h5>);
      continue;
    }
    if (parameter(line)) {
      const start = i;
      const rows = [];
      while (i < lines.length) {
        const text = lines[i].trim();
        if (!text) { i++; continue; }
        const entry = parameter(text);
        if (!entry) break;
        rows.push(<tr key={i} data-definition-source={'prose:'+text}><th scope="row"><Inline text={entry.label}/></th><td><Inline text={entry.value}/></td></tr>);
        i++;
      }
      i--;
      blocks.push(<table className="prose-parameters" key={start}><thead><tr><th scope="col">属性 / 条件</th><th scope="col">数值 / 说明</th></tr></thead><tbody>{rows}</tbody></table>);
      continue;
    }
    const listType = (text: string) => /^第\s*\d+\s*层[：:]/.test(text) ? 'levels'
      : /^\d+[.)、]\s+/.test(text) ? 'ordered' : /^[-*+]\s+/.test(text) ? 'unordered' : null;
    const kind = listType(line);
    if (kind) {
      const start = i;
      const items = [];
      while (i < lines.length && listType(lines[i].trim()) === kind) {
        const raw = lines[i].trim();
        const text = kind === 'levels' ? raw : raw.replace(kind === 'ordered' ? /^\d+[.)、]\s+/ : /^[-*+]\s+/, '');
        items.push(<li key={i} data-definition-source={'prose:'+raw}><RuleText text={text}/></li>);
        i++;
      }
      i--;
      blocks.push(kind === 'ordered' ? <ol key={start} start={Number(line.match(/^\d+/)?.[0])}>{items}</ol>
        : <ul key={start} className={kind === 'levels' ? 'prose-levels' : undefined}>{items}</ul>);
    } else if (line.startsWith('> ')) {
      blocks.push(<blockquote key={i} data-definition-source={'prose:'+line}><RuleText text={line.slice(2)}/></blockquote>);
    } else {
      blocks.push(<p key={i} data-definition-source={'prose:'+line}><RuleText text={line}/></p>);
    }
  }
  return <div className="encyclopedia-prose" data-definition-source={sourceKey}>{blocks}</div>;
}
