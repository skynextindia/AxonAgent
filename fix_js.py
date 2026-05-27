#!/usr/bin/env python3
import re

path = 'cli/static/index.html'
content = open(path, encoding='utf-8').read()

# Find and fix the broken innerHTML assignment with double-quotes inside single-quoted JS string
# Pattern: this.consoleBody.innerHTML = '<div style="...">...'
# Fix: use backtick template literal with single-quoted style attributes

pattern = r"""this\.consoleBody\.innerHTML = '<div style="color:var\(--cyan\);opacity:0\.6;font-size:10px;">\[STREAM_CLEARED\] Active\.</div>';"""
replacement = """this.consoleBody.innerHTML = `<div style='color:var(--cyan);opacity:0.6;font-size:10px;'>[STREAM_CLEARED] Active.</div>`;"""

if re.search(pattern, content):
    content = re.sub(pattern, replacement, content)
    open(path, 'w', encoding='utf-8').write(content)
    print("FIXED: innerHTML string repaired")
else:
    # Try finding it raw
    idx = content.find('STREAM_CLEARED')
    if idx >= 0:
        print("Found STREAM_CLEARED at", idx)
        print(repr(content[idx-80:idx+80]))
    else:
        print("NOT FOUND at all")
