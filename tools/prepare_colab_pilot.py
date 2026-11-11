"""Export a compact, provenance-bearing Colab input and a self-contained notebook."""
import argparse
import hashlib
import json
from pathlib import Path

MODEL = 'Qwen/Qwen3-32B-AWQ'


def export_legacy(packet):
    selected=[]
    excluded=[]
    player_contexts = {}
    for item in packet['evidence']:
        if item['source']=='get_player_context':
            player_contexts[item['arguments']['player_identifier']]=json.loads(item['result'])
    for evidence in packet['evidence']:
        name=evidence['source']
        if name in {'get_team_history','get_team_cap_sheet','get_player_contract','get_league_transactions','get_trade_rumors','search_league_memory','get_player_season_stats'}:
            excluded.append(dict(source=name,arguments=evidence['arguments'],reason='Deferred optional history/contract research for initial assigned-matchup smoke test'))
            continue
        if name=='get_player_scheme_splits' and player_contexts.get(evidence['arguments']['player_identifier'],{}).get('position') != 'QB':
            excluded.append(dict(source=name,arguments=evidence['arguments'],reason='Initial smoke test keeps QB splits; other skill-player splits deferred'))
            continue
        row=dict(evidence)
        try:
            row['result']=json.loads(row['result'])
        except (TypeError,ValueError):
            pass
        if name=='get_player_context' and isinstance(row['result'],dict):
            keep={'player_id','name','position','team','age','years_of_experience','height','weight','madden_ovr','career_awards','season_stats','games_played','postseason_stats','postseason_note','five_year_statline_summary'}
            row['omitted_fields'] = sorted(set(row['result'])-keep)
            row['result']={k:v for k,v in row['result'].items() if k in keep}
        if name=='get_team_roster_depth_chart' and isinstance(row['result'],dict):
            groups=row['result'].get('depth_chart_by_position_group',{})
            row['result']={'team':row['result']['team'],'season':row['result']['season'],'players_by_group':{group:[{k:v for k,v in player.items() if k in {'name','position','overall_rating','age','experience'}} for player in players] for group,players in groups.items()}}
            row['omitted_fields']=['derived roster-summary counts, tier and age-bracket labels']
        if name=='get_record_book' and isinstance(row['result'],dict):
            keep_categories={'season_rec_tds','season_rec_yards','season_pass_tds','season_pass_yards','season_sacks','season_team_points','season_team_wins'}
            for field in ['franchise_records','records_set_this_season']:
                row['result'][field]=[record for record in row['result'].get(field,[]) if record['category'] in keep_categories]
            row['omitted_categories']='Other record categories deferred; no new claims about them'
        if name=='get_player_scheme_splits' and isinstance(row['result'],dict):
            keep_stats={'pass_yards','pass_tds','interceptions','comp_pct','yards_per_attempt'}
            for dimension,groups in row['result'].get('splits_by_opponent',{}).items():
                for group,values in groups.items():
                    for field in ['per_game','vs_his_average']:
                        values[field]={k:v for k,v in values.get(field,{}).items() if k in keep_stats}
            row['omitted_fields']=['QB rushing/fantasy statistics within splits']
        selected.append(row)
    original=packet['b']['task_instruction'].split('\n\nCANONICAL EVIDENCE DOSSIER:\n',1)[0]
    # Minify embedded assignment-brief JSON without dropping league context.
    decoder=json.JSONDecoder(); cursor=0
    while cursor < len(original):
        start=original.find('{',cursor)
        if start<0: break
        try:
            value,end=decoder.raw_decode(original[start:])
        except ValueError:
            cursor=start+1; continue
        compact=json.dumps(value,separators=(',',':'),ensure_ascii=False)
        original=original[:start]+compact+original[start+end:]
        cursor=start+len(compact)
    task=original+'\n\nCANONICAL EVIDENCE DOSSIER (JSON):\n'+json.dumps(selected,separators=(',',':'),ensure_ascii=False)
    task+='\n\nUse the supplied evidence. No tools are available. Optional history and contract sources were omitted for this initial matchup test; omit unsupported canonical claims about those topics. Creative texture is permitted wherever it does not contradict recorded data.'
    return dict(schema_version=1,assignment=dict(season=packet['season'],week=packet['week'],game_id=packet['game_id'],writer=packet['writer']),
                messages=[dict(role='system',content=packet['b']['system_instruction']),dict(role='user',content=task)],
                selected_sources=[dict(source=e['source'],arguments=e['arguments']) for e in selected],excluded_sources=excluded,
                evaluation_corrections=packet.get('evaluation_corrections'),source_packet_sha256=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest(),
                note='Smoke-test evidence selection differs from earlier Gemini dossier; compare quality under current policy, not earlier cost numbers.')


def export(packet):
    from tools.dossier_cards import build_cards
    compiled=build_cards(packet)
    return dict(schema_version=1,dossier_version=2,
                assignment=dict(season=packet['season'],week=packet['week'],game_id=packet['game_id'],writer=packet['writer']),
                messages=[dict(role='system',content=packet['b']['system_instruction']),dict(role='user',content=compiled['task'])],
                assignment_header=compiled['assignment_header'],fact_cards=compiled['cards'],source_index=compiled['source_index'],
                selected_sources=list(compiled['source_index'].values()),excluded_sources=compiled['omissions'],
                evaluation_corrections=packet.get('evaluation_corrections'),
                source_packet_sha256=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest(),
                model_revision='0499c3ac83fdef8810b907a23894ba91e95eddd8',
                note='Dossier v2: explicit stage and fact cards. Same model revision/sampling as first Colab run; no quality/parity claim until generated output is reviewed.')


def notebook():
    cells=[]
    def md(text):
        cells.append(dict(cell_type='markdown',metadata={},source=text.splitlines(keepends=True)))
    def code(text):
        cells.append(dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=text.splitlines(keepends=True)))
    md('''# Project Mu — Colab dossier pilot
Run cells in order on your A100 40 GB. This loads an open model into Colab; no Gemini API key, database access or Google Drive mounting is required. Upload only the supplied `colab_matchup_input_v2.json`. This is dossier v2; the old input is rejected before model loading.

Candidate: [Qwen3-32B-AWQ](https://huggingface.co/Qwen/Qwen3-32B-AWQ), 4-bit weights, non-thinking mode. GPU compatibility and throughput still require this actual run. This is one article, not a production batch.

Record your starting unit balance before connecting. Previously observed: **200 units; 6.73 units/hour**. The runtime panel is authoritative. Model downloads and loading consume session time too. Finish by downloading the result bundle, then Runtime → Disconnect and delete runtime.
''')
    code('''import time, subprocess, json, hashlib, pathlib, sys, importlib.metadata
SESSION_START = time.time()
print(subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv'],text=True))
START_UNITS = 200.0  # edit to the balance shown now
UNITS_PER_HOUR = 6.73  # edit if the runtime panel shows a different rate
''')
    md('## 1. Install the inference runtime\nThis may take several minutes. After installation, use Runtime → Restart session once (not Disconnect and delete). Rerun the first timing cell, skip this installation cell, then continue below. This clears any previously imported library versions.')
    code("%pip install -q 'vllm==0.10.2' 'transformers==4.55.2' 'tokenizers==0.21.4' 'huggingface-hub==0.34.4'\nprint('Installation complete. Runtime → Restart session; rerun the first timing cell, then skip this cell.')\n")
    md('## 2. Upload input and check context before downloading weights')
    code('''from google.colab import files
expected = {'vllm':'0.10.2','transformers':'4.55.2','tokenizers':'0.21.4','huggingface-hub':'0.34.4'}
for package, version in expected.items():
    try:
        actual = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        raise RuntimeError(f'{package} is not installed in this runtime. Run the installation cell above, then Runtime → Restart session (not Disconnect and delete runtime), rerun the first timing cell and continue here.') from None
    if actual != version:
        raise RuntimeError(f'{package} {actual}, expected {version}. Run the installation cell and restart session.')
import transformers, tokenizers, huggingface_hub
if transformers.__version__ != expected['transformers'] or tokenizers.__version__ != expected['tokenizers']:
    raise RuntimeError('Old libraries remain imported: Runtime → Restart session, rerun the first timing cell, then continue here.')
print('Dependency versions verified:',expected)
uploaded = files.upload()
if len(uploaded) != 1:
    raise ValueError('Upload only colab_matchup_input_v2.json')
input_bytes = next(iter(uploaded.values()))
job = json.loads(input_bytes)
assert job['schema_version'] == 1
if job.get('dossier_version') != 2:
    raise RuntimeError('Wrong input: this is the original dossier. Upload colab_matchup_input_v2.json; do not load the model yet.')
print('DOSSIER VERSION:',job['dossier_version'])
print(job['assignment_header'])
assert [m['role'] for m in job['messages']] == ['system','user']
MODEL_ID = 'Qwen/Qwen3-32B-AWQ'
MAX_MODEL_LEN = 40960
MAX_NEW_TOKENS = 4096
from huggingface_hub import model_info
from transformers import AutoTokenizer
# Resolve one immutable model commit for tokenizer and weights.
MODEL_REVISION = job.get('model_revision') or model_info(MODEL_ID).sha
TOKENIZER_START = time.time()
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False)
if not hasattr(tokenizer, 'all_special_tokens_extended'):
    raise RuntimeError('Tokenizer API incompatible with pinned vLLM; stop before loading weights.')
prompt_ids = tokenizer.apply_chat_template(job['messages'], tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
print('Prompt tokens:',len(prompt_ids),'Output allowance:',MAX_NEW_TOKENS)
if len(prompt_ids) + MAX_NEW_TOKENS > MAX_MODEL_LEN:
    raise RuntimeError('Input exceeds the configured context. Stop and send the token count; nothing will be truncated.')
print('Input fits configured context. Proceed to load weights.')
''')
    md('## 3. Load the model\nThe initial download is large. Keep the notebook open. If loading fails, send the error rather than repeatedly retrying. No paid API fallback is configured.')
    code('''import torch
from vllm import LLM, SamplingParams
if not torch.cuda.is_available():
    raise RuntimeError('No GPU connected')
if torch.cuda.get_device_properties(0).total_memory < 39 * 1024**3:
    raise RuntimeError('This configuration expects the observed A100 40 GB; stop if hardware changed')
LOAD_START = time.time()
llm = LLM(model=MODEL_ID, revision=MODEL_REVISION, tokenizer_revision=MODEL_REVISION,
          quantization='awq', dtype='half', tensor_parallel_size=1,
          max_model_len=MAX_MODEL_LEN, gpu_memory_utilization=0.90,
          max_num_seqs=1, max_num_batched_tokens=2048,
          enable_chunked_prefill=True, enforce_eager=True,
          trust_remote_code=False, seed=42)
LOAD_SECONDS = time.time() - LOAD_START
print('Model load seconds:',round(LOAD_SECONDS,1))
print(subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.used,memory.total','--format=csv'],text=True))
''')
    md('## 4. Generate and save one article\nCreative scenes, quotes, access and unrecorded plays are allowed when they do not contradict canonical facts. A completed generation is not a factual approval.')
    code('''params = SamplingParams(temperature=0.7, top_p=0.8, top_k=20, min_p=0,
                        repetition_penalty=1.0, max_tokens=MAX_NEW_TOKENS, seed=42)
GEN_START = time.time()
outputs = llm.generate([{'prompt_token_ids':prompt_ids}], params)
GEN_SECONDS = time.time() - GEN_START
answer = outputs[0].outputs[0]
article = answer.text
if not article.strip():
    raise RuntimeError('Empty article; inspect output rather than publishing')
output_dir = pathlib.Path('/content/project_mu_result')
output_dir.mkdir(exist_ok=True)
(output_dir/'article.md').write_text(article)
report = dict(model=MODEL_ID,revision=MODEL_REVISION,input_sha256=hashlib.sha256(input_bytes).hexdigest(),
              dossier_version=job.get('dossier_version',1),assignment=job['assignment'],source_packet_sha256=job['source_packet_sha256'],
              prompt_tokens=len(prompt_ids),output_tokens=len(answer.token_ids),finish_reason=answer.finish_reason,
              output_capped=answer.finish_reason=='length',load_seconds=LOAD_SECONDS,generation_seconds=GEN_SECONDS,
              session_elapsed_seconds=time.time()-SESSION_START,
              estimated_units_since_first_cell=(time.time()-SESSION_START)/3600*UNITS_PER_HOUR,
              starting_units=START_UNITS,displayed_units_per_hour=UNITS_PER_HOUR,
              max_model_len=MAX_MODEL_LEN,sampling=params.__repr__(),
              packages={p:importlib.metadata.version(p) for p in ['vllm','torch','transformers']},
              gpu_snapshot=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.used,memory.total','--format=csv'],text=True),
              factual_status='unreviewed',limitations=['Unit estimate excludes time before first cell and is not account balance reconciliation',
              'nvidia-smi snapshot is not measured session peak VRAM','First model generation; no warm-throughput claim'])
(output_dir/'report.json').write_text(json.dumps(report,indent=2))
(output_dir/'input.json').write_bytes(input_bytes)
print(article)
print('Generation seconds:',round(GEN_SECONDS,1),'Finish reason:',answer.finish_reason)
''')
    md('## 5. Download results, then stop the runtime\nNote your ending compute-unit balance and any errors. The ZIP contains the article, report and exact input. Send it back for factual/editorial review. Then use **Runtime → Disconnect and delete runtime**.')
    code('''import shutil
END_UNITS = None  # optionally enter the balance shown now
report['ending_units'] = END_UNITS
report['actual_units_used'] = START_UNITS-END_UNITS if END_UNITS is not None else None
(output_dir/'report.json').write_text(json.dumps(report,indent=2))
bundle = '/content/project_mu_colab_result_v' + str(job.get('dossier_version',1))
shutil.make_archive(bundle,'zip',output_dir)
files.download(bundle+'.zip')
''')
    for index, cell in enumerate(cells):
        cell['id']=f'cell-{index:02d}'
    return dict(nbformat=4,nbformat_minor=5,metadata=dict(colab=dict(name='Project Mu — Colab dossier pilot'),kernelspec=dict(name='python3',display_name='Python 3'),language_info=dict(name='python')),cells=cells)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packet',type=Path)
    args=parser.parse_args()
    packet=json.loads(args.packet.read_text())
    job=export(packet)
    out=Path('verify_output/v3_colab');out.mkdir(parents=True,exist_ok=True)
    (out/'colab_matchup_input.json').write_text(json.dumps(job,indent=2,ensure_ascii=False))
    (out/'colab_matchup_input_v2.json').write_text(json.dumps(job,indent=2,ensure_ascii=False))
    Path('notebooks').mkdir(exist_ok=True)
    (Path('notebooks')/'newsroom_dossier_pilot.ipynb').write_text(json.dumps(notebook(),indent=2,ensure_ascii=False))
    (out/'newsroom_dossier_pilot_v2.ipynb').write_text(json.dumps(notebook(),indent=2,ensure_ascii=False))
    print('Exported',len(job['selected_sources']),'sources;',len(job['messages'][1]['content']),'task characters')


if __name__=='__main__':
    main()
