#!/usr/bin/env python3
"""Extract script block and validate it with node.js"""
import re, subprocess, sys

content = open('cli/static/index.html', encoding='utf-8').read()

# Find the big script
scripts = list(re.finditer(r'<script>', content))
for m in scripts:
    end = content.find('</script>', m.end())
    body = content[m.end():end]
    if len(body) > 1000:
        print(f'Script: len={len(body)}')
        # Write to temp file
        with open('_tmp_script.js', 'w', encoding='utf-8') as f:
            f.write(body)
        print('Written to _tmp_script.js')
        break

# Try to parse with node
result = subprocess.run(['node', '--check', '_tmp_script.js'], 
                       capture_output=True, text=True)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Return code:", result.returncode)
