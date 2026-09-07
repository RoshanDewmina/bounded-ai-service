import hashlib
import json
import subprocess
from datetime import datetime,timezone
from pathlib import Path

from .core import ROOT
from .evaluate import score_case


def main():
    dataset=json.loads((ROOT/'data/eval.json').read_text())
    cases={c['id']:c for c in dataset['test']}
    for provider in ('baseline','model'):
        source=ROOT/'evidence'/('evaluation-'+provider+'.json')
        original=json.loads(source.read_text())
        rows=[]
        for row in original['cases']:
            rows.append({**row,**score_case(cases[row['case_id']],row['result'])})
        measured={**original['results'],'task_success_count':sum(r['task_success'] for r in rows),'task_success_rate':sum(r['task_success'] for r in rows)/len(rows),'tool_selection_success_count':sum(r['tool_selection_success'] for r in rows),'positive_task_count':sum(cases[r['case_id']]['expected_status']=='ok' for r in rows),'positive_task_success_count':sum(r['task_success'] and cases[r['case_id']]['expected_status']=='ok' for r in rows)}
        output={**original,'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'dirty_tree':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),'command':'uv run python3 -m bounded_ai.rescore','timestamp':datetime.now(timezone.utc).isoformat(),'measurement_kind':'deterministic_rescore_of_preserved_real_execution_traces','execution_source_revision':original['source_revision'],'trace_source':source.name,'trace_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'label_version':dataset['version'],'metric_revision':dataset['metric_revision'],'label_sha256':hashlib.sha256((ROOT/'data/eval.json').read_bytes()).hexdigest(),'results':measured,'cases':rows,'limitations':original['limitations']+['Scorer strengthened after independent review; no provider prompts changed and no hidden rerun','Original execution receipts remain unchanged; substantive labels are versioned']}
        destination=ROOT/'evidence'/('rescored-'+provider+'.json');destination.write_text(json.dumps(output,indent=2)+'\n')
        print(provider,json.dumps(measured))


if __name__=='__main__':main()
