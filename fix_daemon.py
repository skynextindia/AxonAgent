with open('axonai/realtime/daemon.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith('                  AGENT_NAME_MAP = {'):
        # We know it's at an indentation level. The previous loop was indented with 20 spaces probably.
        # Let's just strip leading spaces and add the correct amount based on context.
        pass

# Actually, the simplest way is to re-indent the AGENT_NAME_MAP block.
import re
with open('axonai/realtime/daemon.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix indentation of AGENT_NAME_MAP
def fix_indent(match):
    lines = match.group(0).split('\n')
    # Find the indent of 'for node, content_val in chunk.items():'
    last_line = lines[-1]
    indent = len(last_line) - len(last_line.lstrip())
    new_lines = []
    for line in lines[:-1]:
        if line.strip():
            new_lines.append(' ' * indent + line.lstrip())
        else:
            new_lines.append(line)
    new_lines.append(last_line)
    return '\n'.join(new_lines)

fixed_content = re.sub(r'(?sm)                  AGENT_NAME_MAP.*?for node, content_val in chunk\.items\(\):', fix_indent, content)
with open('axonai/realtime/daemon.py', 'w', encoding='utf-8') as f:
    f.write(fixed_content)
