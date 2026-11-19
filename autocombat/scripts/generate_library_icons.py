"""Generate the library's code-native SVG assets. Run after adding contestants."""
from pathlib import Path
import sys
from html import escape
import math
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
PROFILES = json.loads((ROOT / 'frontend/src/data/encyclopedia.json').read_text(encoding='utf-8'))
from characters_data import ALL_CHARACTERS

# Each emblem has its own silhouette, rather than wrapping at twelve entries.
PATHS = {
 'star': 'M32 5L39 23L59 24L44 37L49 58L32 46L15 58L20 37L5 24L25 23Z',
 'flame': 'M32 5C41 21 53 26 49 43C45 60 19 61 14 44C10 32 19 22 25 17C23 30 29 33 31 35C37 27 34 17 32 5Z',
 'meteor': 'M7 7L30 20M20 6L40 18M6 20L22 35M28 26C45 9 65 33 50 48C36 63 14 43 28 26ZM34 31L45 42M43 29L32 42',
 'horn': 'M17 9C3 14 6 31 20 33M47 9C61 14 58 31 44 33M19 25L25 20H39L45 25L41 48L32 56L23 48ZM25 33L28 35M39 33L36 35M28 45H36',
 'microphone': 'M25 8C39 0 50 17 39 27L29 35L17 23ZM17 23L29 35L15 51L6 44ZM39 36L45 50L56 41M46 14L55 7M49 25H60',
 'bat': 'M31 20L26 10L22 24C12 15 5 19 3 30L6 46Q13 35 21 47Q30 41 32 56Q34 41 43 47Q51 35 58 46L61 30C59 19 52 15 42 24L38 10L33 20ZM27 30L30 33M37 30L34 33',
 'snow': 'M32 3V61M7 17L57 47M7 47L57 17M23 8L32 17L41 8M23 56L32 47L41 56M7 27L19 25L17 13M47 51L45 39L57 37M7 37L19 39L17 51M47 13L45 25L57 27',
 'snowman': 'M23 15H42M26 15V5H38V15M21 26A11 11 0 1 0 43 26A11 11 0 1 0 21 26M16 47A16 13 0 1 0 48 47A16 13 0 1 0 16 47M26 36H43M33 25L40 28L33 30M16 42L5 34M48 42L59 34',
 'flower': 'M32 25C13 0 6 23 23 32C-1 43 24 60 32 40C40 60 65 43 41 32C58 23 51 0 32 25ZM26 32A6 6 0 1 0 38 32A6 6 0 1 0 26 32M32 43V61M32 55L43 49',
 'cake': 'M8 30L31 17L56 30V53H8ZM8 38H56M9 45Q16 51 24 45T40 45T55 45M31 17V8M28 5Q31 0 34 5M20 26L43 26',
 'branch': 'M31 60V24M31 45L12 27M31 35L49 17M16 12Q29 12 31 27Q16 28 16 12ZM46 31Q59 29 58 44Q44 46 46 31ZM6 28Q18 29 19 42Q5 42 6 28ZM35 7Q48 4 47 17Q35 21 35 7Z',
 'hummingbird': 'M4 31L27 26L23 9Q42 14 42 29L58 21L48 34L60 43L37 39L20 56L26 38L4 31ZM38 23L41 23',
 'scythe': 'M13 59L43 13M12 21Q32-2 58 15Q39 6 23 29ZM28 35L37 41',
 'portal': 'M15 32A17 27 0 1 0 49 32A17 27 0 1 0 15 32M23 32A9 19 0 1 0 41 32A9 19 0 1 0 23 32M3 32H26M20 25L27 32L20 39M49 10L57 18M49 54L57 46',
 'giant': 'M23 12L32 5L41 12L39 26L51 32L55 51H42L38 38V59H26V38L22 51H9L13 32L25 26ZM27 17H37',
 'cartridge': 'M13 7H51V57H13ZM21 7V26H43V7M22 57V41H42V57M27 14H37M27 20H37M6 30H18M46 34H58M52 28V40',
 'cloak': 'M23 9Q32 1 41 9L38 20L53 55Q32 48 11 55L26 20ZM24 20H40M31 26L26 39L33 36L29 48L41 32L34 35L38 26',
 'quill': 'M9 56L42 14M18 45Q14 22 39 7Q59 2 55 17Q48 39 26 41ZM30 31H44M34 24V14M8 58H37M43 49A9 9 0 1 0 57 37A12 12 0 0 1 43 49',
 'bloodmoon': 'M47 7A26 26 0 1 0 57 43A25 25 0 0 1 47 7ZM28 19C21 30 16 35 18 42C22 55 40 49 39 39C38 33 32 25 28 19Z',
}

def svg(body, title):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>{escape(title)}</title><g fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>\n'

def main():
    out = ROOT / 'frontend/public/icons'; out.mkdir(parents=True, exist_ok=True)
    for emblem, path in PATHS.items():
        (out / f'{emblem}.svg').write_text(svg(f'<path d="{path}"/>', emblem), encoding='utf-8')
    for c in ALL_CHARACTERS:
        emblem = PROFILES[c['serial']]['emblem']
        for i, s in enumerate(c['spells'], 1):
            # Individual skill asset: character silhouette, action marker, index
            # and a progress arc. The ordinal distinguishes forms and variants.
            end = i * 2 * math.pi / (len(c['spells']) + 1)
            x, y = 32 + 29*math.sin(end), 32 - 29*math.cos(end)
            badge = '<path d="M44 45H58M51 38V52"/>' if s['category'] == 3 else '<path d="M46 40L57 51M57 40L46 51"/>' if s['category'] == 1 else '<circle cx="51" cy="45" r="8"/>'
            body = f'<g transform="translate(5 4) scale(.72)"><path d="{PATHS[emblem]}"/></g><path d="M32 3A29 29 0 {int(end > math.pi)} 1 {x:.2f} {y:.2f}"/>{badge}<text x="10" y="60" fill="currentColor" stroke="none" font-family="sans-serif" font-size="12">{i:02}</text>'
            (out / f"{c['serial']}-s{i}.svg").write_text(svg(body, c['name']+' · '+s['name']), encoding='utf-8')

if __name__ == '__main__':
    main()
