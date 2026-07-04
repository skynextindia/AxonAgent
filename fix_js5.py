#!/usr/bin/env python3
"""Find and fix corrupted class= patterns in JS script block"""
import re, subprocess

path = 'cli/static/index.html'
content = open(path, encoding='utf-8').read()

# Pattern: class="xyz' (double-quote open, single-quote close - corrupted by our fixes)
# Should be: class="xyz" (both double-quote)
# The corruption: fix_js2 changed class="x" -> class='x', then fix_js4 reverted class=' -> class="
# but the closing ' was the JS string terminator, so now we have class="x'

# Find all occurrences of class="...' pattern in script block
scripts = list(re.finditer(r'<script>', content))
script_m = None
for m in scripts:
    end = content.find('</script>', m.end())
    body = content[m.end():end]
    if len(body) > 1000:
        script_m = m
        script_end = end
        break

script_body = content[script_m.end():script_end]

# The broken pattern: class="[content]' where ' terminates the outer JS string
# Fix: the [content] should end with " not '
# Pattern: class="([^"']+)' -> class="\1"
# But we need to be careful: only fix inside JS string literals
# Heuristic: fix class="...stuff...' -> class="...stuff"
# The ' at the end is the JS string terminator that got confused with attribute closing

corrupted = re.findall(r'class="([^"\'<>]+)\'', script_body)
print(f"Found {len(corrupted)} potentially corrupted class= patterns in script")
for c in corrupted[:10]:
    print(f"  class=\"{c}'")

# Fix: replace class="X' with class="X"
# But this might close attribute wrong if X itself contains unrelated content
# Safer: replace pattern '...<span class="X'>...' -> '...<span class="X">...'
# i.e., fix the ' that immediately follows the class attribute value (no spaces/quotes in value)

fixed = re.sub(r'(class="[^"\'<>\s]+)\'>', r'\1">', script_body)
print(f"\nFixes applied (checked):")
print(f"  Before: class= corrupted = {len(re.findall(r'class=\"[^\"\'<>]+\'>', script_body))}")
print(f"  After:  class= corrupted = {len(re.findall(r'class=\"[^\"\'<>]+\'>', fixed))}")

# Write back
before = content[:script_m.end()]
after = content[script_end:]
new_content = before + fixed + after
open(path, 'w', encoding='utf-8').write(new_content)

# Validate
with open('_tmp_script.js', 'w', encoding='utf-8') as f:
    f.write(fixed)
result = subprocess.run(['node', '--check', '_tmp_script.js'], capture_output=True, text=True)
if result.returncode == 0:
    print("\nNode.js syntax check: PASS - JS is valid!")
else:
    print("\nNode.js syntax check: FAIL")
    # Show first error
    lines = result.stderr.split('\n')
    for l in lines[:10]:
        print(l)
