import json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
results={}
for pattern in ['test_acceleration.py','test_sim_prelim.py','test_ui*.py','test_settings.py']:
    name=pattern.replace('*','all').replace('.py','')
    p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',pattern],
        cwd=root,capture_output=True,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    (root/f'artifacts/performance/{name}.log').write_bytes(p.stdout+p.stderr)
    results[pattern]=p.returncode
    print(pattern,p.returncode,flush=True)
    (root/'artifacts/performance/checks.json').write_text(json.dumps(results,indent=2))
