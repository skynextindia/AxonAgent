import json
import ast

transcript_path = r'C:\Users\ashwi\.gemini\antigravity-ide\brain\e97af84e-652f-450c-a274-33f357af311a\.system_generated\logs\transcript.jsonl'
file_path = r'd:\work\TradingAgents\axonai\realtime\daemon.py'

def parse_val(val):
    if isinstance(val, str) and (val.startswith('"') or val.startswith('[')):
        try:
            return json.loads(val)
        except:
            return val
    return val

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

count = 0
applied = 0
with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            entry = json.loads(line)
            calls = entry.get('tool_calls', [])
            for call in calls:
                if call.get('name') in ['replace_file_content', 'multi_replace_file_content']:
                    args = call.get('args', {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except: pass
                    
                    target = parse_val(args.get('TargetFile', ''))
                    if 'daemon.py' in target:
                        count += 1
                        if count > 10:
                            print(f'Skipping edit {count}')
                            continue
                            
                        print(f'Applying edit {count}...')
                        
                        if call.get('name') == 'replace_file_content':
                            tc = parse_val(args.get('TargetContent'))
                            rc = parse_val(args.get('ReplacementContent'))
                            if tc and tc in content:
                                content = content.replace(tc, rc, 1)
                                applied += 1
                                print('  -> Success')
                            else:
                                print('  -> Failed! tc not found')
                        elif call.get('name') == 'multi_replace_file_content':
                            chunks = parse_val(args.get('ReplacementChunks', []))
                            if isinstance(chunks, str):
                                chunks = json.loads(chunks)
                            for i, chunk in enumerate(chunks):
                                tc = chunk.get('TargetContent')
                                rc = chunk.get('ReplacementContent')
                                if tc and tc in content:
                                    content = content.replace(tc, rc, 1)
                                    applied += 1
                                    print(f'  -> Chunk {i} success')
                                else:
                                    print(f'  -> Chunk {i} Failed!')
        except Exception as e:
            pass

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Applied {applied} edits to daemon.py')
