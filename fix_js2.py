#!/usr/bin/env python3
"""
Fix ALL innerHTML/outerHTML strings in the <script> block that contain 
unescaped double-quote HTML attributes, which break the browser's HTML tokenizer.
Strategy: find script content, replace style="..." with style='...' inside JS string literals.
"""
import re

path = 'cli/static/index.html'
content = open(path, encoding='utf-8').read()

# Extract the script block (the large one)
script_start = content.find('\n        // High-contrast realtime WebSocket UI controller')
script_end_marker = '</script>'
script_end = content.find(script_end_marker, script_start)

before = content[:script_start]
script_body = content[script_start:script_end]
after = content[script_end:]

print(f"Script body length: {len(script_body)}")

original_script = script_body

# Strategy: inside JS strings that contain HTML (detected by innerHTML/outerHTML = `...` or '...')
# Replace style="..." with style='...' to avoid HTML attribute confusion
# Also replace class="..." with class='...' in innerHTML strings

# Pattern 1: single-quoted HTML strings with double-quoted attributes
# Replace: = '<tag ... attr="val" ...'  =>  = '<tag ... attr=\'val\' ...'
# This is complex - safer approach: convert all innerHTML string literals to template literals
# and change inner double-quotes to single-quotes

# Actually the cleanest fix: in the script body, find all occurrences of 
# style="  and class=" within JS string contexts and replace " with '
# Simple heuristic: replace all style=" with style=' and matching closing "
# within innerHTML/insertAdjacentHTML/outerHTML contexts

import re

def fix_html_in_js_strings(script):
    """Replace double-quoted HTML attributes with single-quoted ones in JS strings."""
    result = script
    
    # Fix pattern: 'color:var(--cyan)' etc style attributes using double quotes
    # Replace all occurrences of: style="[^"]*" -> style='[^']*'  
    # within the JS code (which would break HTML tokenizer)
    
    # More targeted: find string literals containing HTML and fix them
    # Replace double-quoted style/class attributes inside JS string literals
    
    # Replace style="..." inside innerHTML string content
    result = re.sub(r'style="([^"]*)"', r"style='\1'", result)
    result = re.sub(r'class="([^"]*)"', r"class='\1'", result)
    result = re.sub(r'placeholder="([^"]*)"', r"placeholder='\1'", result)
    
    return result

fixed_script = fix_html_in_js_strings(script_body)

changes = 0
for i, (a, b) in enumerate(zip(script_body, fixed_script)):
    if a != b:
        changes += 1

if fixed_script != original_script:
    content = before + fixed_script + after
    open(path, 'w', encoding='utf-8').write(content)
    # Count replacements roughly
    orig_dq = script_body.count('style="') + script_body.count('class="')
    fixed_dq = fixed_script.count("style='") + fixed_script.count("class='")
    print(f"FIXED: replaced HTML double-quote attributes with single-quotes")
    print(f"  style= replacements: {script_body.count('style=\"')} -> {fixed_script.count('style=')}")
    print(f"  class= replacements: {script_body.count('class=\"')} -> fixed")
else:
    print("No changes needed")
