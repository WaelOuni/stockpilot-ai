"""Collect bounded redacted runtime evidence and real read-only catalogue benchmarks."""
import json
from pathlib import Path
import statistics
import subprocess

from scripts.candidate.manage import DEFAULT, PROJECT, verify
from scripts.candidate.runtime import api_python


def main():
    manifest=verify(DEFAULT)
    logs=subprocess.check_output(['docker','logs',PROJECT+'-api-1'],text=True,stderr=subprocess.DEVNULL)
    allowed={'event','timestamp','level','request_id','duration_ms','call_type','model','intent','error_type','thread_id','path','status_code'}
    events=[]
    for line in logs.splitlines():
        try: value=json.loads(line)
        except ValueError: continue
        events.append({key:val for key,val in value.items() if key in allowed})
    records=json.loads((DEFAULT/'validation-results.json').read_text())
    requests=[]
    for record in records:
        if record['scenario']!='request': continue
        associated=[event for event in events if event.get('request_id')==record['request_id']]
        durations={kind:sum(event.get('duration_ms',0) for event in associated if event.get('event')==f'llm_{kind}_completed') for kind in ('classification','generation')}
        requests.append({**record,**{kind+'_ms':duration for kind,duration in durations.items()},
            'llm_calls_started':sum(event.get('event') in ('llm_classification_started','llm_generation_started') for event in associated)})
    benchmarks=api_python('''
import asyncio,time,statistics
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.application.use_cases.catalogue_advice import extract_criteria
repo=MongoProductRepository(db)
criteria=extract_criteria('Je cherche une perceuse disponible à moins de 100 euros.')
async def benchmark():
    search=[];verification=[]
    for _ in range(20):
        started=time.perf_counter(); candidates=await repo.find_advice_candidates(criteria)
        search.append((time.perf_counter()-started)*1000)
        started=time.perf_counter()
        for product in candidates: await repo.find_advice_by_sku(product.sku)
        verification.append((time.perf_counter()-started)*1000)
    return {'samples':20,'search_median_ms':statistics.median(search),'verification_three_median_ms':statistics.median(verification),
        'search_max_ms':max(search),'verification_three_max_ms':max(verification)}
print(json.dumps(asyncio.run(benchmark())))
''')
    models_command='import json,urllib.request; print(json.dumps({"tags":json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/tags",timeout=10)),"version":json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/version",timeout=10)),"loaded":json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/ps",timeout=10))}))'
    models=json.loads(subprocess.check_output(['docker','exec',PROJECT+'-relay-1','python','-c',models_command],text=True))
    report={'application_revision':manifest['revision'],'models':models,'requests':requests,'catalogue_benchmarks':benchmarks,
            'api_error_events':[e for e in events if e.get('level') in ('ERROR','CRITICAL')],
            'limits':'LLM timings are correlated native logs; DB timings are separate warm read-only microbenchmarks. OTEL export disabled; no production trace backend.'}
    path=DEFAULT/'observations.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2));path.chmod(0o600)
    print(json.dumps({'requests':len(requests),'ollama_version':models['version'],'catalogue_benchmarks':benchmarks,
        'total_median_ms':statistics.median(r['total_ms'] for r in requests),'total_max_ms':max(r['total_ms'] for r in requests)},ensure_ascii=False))


if __name__ == '__main__': main()
