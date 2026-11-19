"""Rebuild original vector figures/backgrounds and deterministic PCM music."""
from pathlib import Path
import math
import struct
import wave

ROOT=Path(__file__).resolve().parents[1]/'story-projects/demo'


def write_backgrounds():
    for name,colors in {'courtyard':('#f6e9dc','#a8c4bc','#d19691'),'observatory':('#263047','#68648f','#c8badb'),'dawn':('#f6dfca','#baafcc','#f1ab7f')}.items():
        a,b,c=colors
        stars=''.join(f'<circle cx="{(i*173+71)%1600}" cy="{(i*83+29)%620}" r="{1+i%3}" fill="#fff7df" opacity=".55"/>' for i in range(45))
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900"><defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient><radialGradient id="glow"><stop stop-color="{c}" stop-opacity=".5"/><stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient></defs><rect width="1600" height="900" fill="url(#sky)"/>{stars}<circle cx="1100" cy="220" r="290" fill="url(#glow)"/><circle cx="1100" cy="220" r="60" fill="#fff3dc" opacity=".8"/><path d="M0 610Q250 520 500 610T1000 600T1600 630V900H0Z" fill="{b}"/><path d="M0 730Q500 590 1600 760V900H0Z" fill="{a}" opacity=".3"/><path d="M1060 620V330L1100 280L1140 330V620Z" fill="#36394c" opacity=".65"/><path d="M1045 337H1155L1100 265Z" fill="#454057"/><rect x="1072" y="345" width="56" height="38" rx="5" fill="#ffdeb0"/><path d="M0 815H1600M0 850H1600" stroke="#fff" stroke-opacity=".2"/><path d="M120 720V500M280 720V480M0 535H380" stroke="{c}" stroke-width="9" opacity=".5"/><path d="M100 500Q200 355 300 480" stroke="{c}" stroke-width="8" fill="none" opacity=".5"/></svg>'''
        p=ROOT/'background'/f'{name}.svg';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(svg,encoding='utf-8')


def write_figures():
    for i,(name,color,hair) in enumerate([('guide','#be858d','#705169'),('partner','#8c85b5','#383e59'),('scout','#78a99f','#6c6359'),('healer','#b2a46f','#b08c70'),('observer','#7897b5','#4b576d')]):
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="520" height="900" viewBox="0 0 520 900"><defs><linearGradient id="dress" x2="1" y2="1"><stop stop-color="{color}"/><stop offset="1" stop-color="#f5ece4"/></linearGradient></defs><ellipse cx="260" cy="870" rx="140" ry="18" fill="#313247" opacity=".12"/><path d="M211 665L200 853Q220 874 241 855L258 665M269 665L280 854Q302 874 322 852L311 665" fill="#ece3df" stroke="#514b62" stroke-width="4"/><path d="M184 850Q205 840 240 849L245 877H183ZM280 849Q310 840 331 854L334 877H278Z" fill="{hair}"/><path d="M155 334Q108 407 91 535L121 549L191 393M362 334Q407 409 429 535L399 550L332 393" fill="#f4d9ce" stroke="#716173" stroke-width="3"/><path d="M97 526Q76 549 94 568Q109 579 123 548M399 543Q416 576 432 558Q443 545 425 526" fill="#f4d9ce"/><path d="M167 323L218 297H302L353 323L333 455Q363 543 386 674Q260 721 134 674Q157 540 187 455Z" fill="url(#dress)" stroke="#716173" stroke-width="4"/><path d="M165 328L198 385L231 331M353 328L321 385L289 331" fill="#f9f2e9"/><path d="M188 448Q260 470 333 448L330 477H190Z" fill="{hair}"/><path d="M260 326L243 371L260 392L277 371Z" fill="#f6dda5"/><path d="M163 675Q260 702 386 674M176 635Q260 658 371 636" stroke="#fff7ed" stroke-width="8" fill="none"/><path d="M214 291L214 323Q260 354 306 323L306 291" fill="#efd0c3"/><path d="M154 201Q130 86 204 64Q272 20 337 88Q392 144 356 276L326 311L179 294Z" fill="{hair}"/><ellipse cx="260" cy="195" rx="86" ry="110" fill="#f7ded0"/><path d="M172 178Q195 98 251 88Q267 167 344 186L350 117Q273 37 202 85Z" fill="{hair}"/><path d="M173 172Q167 261 188 330L155 299L150 176M343 167Q355 254 331 331L367 293L369 173" fill="{hair}"/><path d="M202 198Q222 185 239 199M281 199Q299 186 319 198" stroke="#584e65" stroke-width="5" fill="none"/><ellipse cx="222" cy="205" rx="8" ry="13" fill="{color}"/><ellipse cx="300" cy="205" rx="8" ry="13" fill="{color}"/><circle cx="225" cy="201" r="3" fill="#fff"/><circle cx="303" cy="201" r="3" fill="#fff"/><path d="M243 257Q261 267 279 255" stroke="#bc8a86" stroke-width="4" fill="none"/><ellipse cx="199" cy="237" rx="15" ry="6" fill="#dcaaa3" opacity=".45"/><ellipse cx="321" cy="237" rx="15" ry="6" fill="#dcaaa3" opacity=".45"/><path d="M340 117L354 98L365 120L386 128L365 140L356 162L345 142L323 133Z" fill="#f8e2a8"/></svg>'''
        p=ROOT/'figure'/f'{name}.svg';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(svg,encoding='utf-8')


def synthesize_music():
    for name,notes in {'starlight':[60,64,67,71,69,67,64,62],'battle':[48,55,60,63,58,55,53,55],'dawn':[60,67,72,76,74,71,67,64]}.items():
        rate=22050;duration=16;data=bytearray()
        for i in range(rate*duration):
            t=i/rate;step=int(t/2)%len(notes);freq=440*2**((notes[step]-69)/12);phase=t%2
            envelope=min(1,phase/.06)*math.exp(-phase*1.3)
            chord=sum(math.sin(2*math.pi*freq*ratio*t)*amp for ratio,amp in [(1,.12),(1.5,.04),(2,.025)])
            data.extend(struct.pack('<h',int(24000*chord*envelope)))
        p=ROOT/'bgm'/f'{name}.wav';p.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(p),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(data)
    p=ROOT/'vocal/chime.wav';p.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(p),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(22050);f.writeframes(b''.join(struct.pack('<h',int(2500*math.sin(2*math.pi*880*i/22050)*math.exp(-i/5000))) for i in range(11025)))


if __name__=='__main__':
    write_backgrounds();write_figures();synthesize_music();print('Generated original story assets')
