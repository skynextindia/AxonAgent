#!/usr/bin/env python3
"""
Targeted fix: only modify innerHTML/insertAdjacentHTML/outerHTML string literals
within the script block to use single-quoted HTML attributes.
Does NOT touch the HTML portion of the file.
"""
import re

path = 'cli/static/index.html'
content = open(path, encoding='utf-8').read()

# Find the script block boundaries
script_open = content.find('\n        // High-contrast realtime WebSocket UI controller')
script_close_marker = '\n    </script>'
script_close = content.find(script_close_marker, script_open)

if script_open < 0 or script_close < 0:
    print("ERROR: Could not find script block")
    exit(1)

before = content[:script_open]
script_body = content[script_open:script_close]
after = content[script_close:]

print(f"Script body length: {len(script_body)}")
print(f"HTML before length: {len(before)}")

# Count existing issues in script
print(f"  Double-quoted class= in script: {script_body.count('class=\"')}")
print(f"  Double-quoted style= in script: {script_body.count('style=\"')}")

# Fix: replace class="..." and style="..." within the script block ONLY
# These are inside JS string literals (innerHTML assignments) and break the HTML tokenizer
fixed_script = script_body
fixed_script = re.sub(r'class="([^"]*)"', r"class='\1'", fixed_script)
fixed_script = re.sub(r'style="([^"]*)"', r"style='\1'", fixed_script)
fixed_script = re.sub(r'placeholder="([^"]*)"', r"placeholder='\1'", fixed_script)

# Check the HTML portion is unchanged
print(f"\nHTML before/after check:")
print(f"  Before unchanged: {before == content[:script_open]}")

# Rebuild
new_content = before + fixed_script + after

if new_content != content:
    open(path, 'w', encoding='utf-8').write(new_content)
    print(f"\nFIXED: {script_body.count('class=\"')} class= and {script_body.count('style=\"')} style= fixed in script block only")
    print(f"HTML portion UNTOUCHED")
else:
    print("No changes made")

# Verify HTML is unchanged  
new_before = new_content[:script_open]
if new_before == before:
    print("✓ HTML portion verified unchanged")
else:
    print("✗ WARNING: HTML portion changed!")
