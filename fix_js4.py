#!/usr/bin/env python3
"""
Revert: class=' -> class=" in JS script block only.
fix_js2.py broke single-quoted JS strings by changing class="..." to class='...'
"""
import re, subprocess

path = 'cli/static/index.html'
content = open(path, encoding='utf-8').read()

# Find the big script block
scripts = list(re.finditer(r'<script>', content))
script_m = None
for m in scripts:
    end = content.find('</script>', m.end())
    body = content[m.end():end]
    if len(body) > 1000:
        script_m = m
        script_end = end
        break

before = content[:script_m.end()]
script_body = content[script_m.end():script_end]
after = content[script_end:]

print(f"Script length: {len(script_body)}")
old_count = script_body.count("class='")
print(f"class=single-quote occurrences to revert: {old_count}")

# Revert: class=' -> class=" in script body only
fixed = script_body.replace("class='", 'class="')

# Also revert placeholder=' that may have been changed
fixed = fixed.replace("placeholder='", 'placeholder="')

# Write back
new_content = before + fixed + after
open(path, 'w', encoding='utf-8').write(new_content)
print(f"Reverted {old_count} class=' back to class=double-quote in script only")

# Validate with node
with open('_tmp_script.js', 'w', encoding='utf-8') as f:
    f.write(fixed)
result = subprocess.run(['node', '--check', '_tmp_script.js'], capture_output=True, text=True)
if result.returncode == 0:
    print("Node.js syntax check: PASS")
else:
    print("Node.js syntax check: FAIL")
    print(result.stderr)
