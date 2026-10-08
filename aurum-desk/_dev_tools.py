import sys, os, re

sys.stdout.reconfigure(encoding='utf-8')

def cat(filepath, start=1, end=None):
    if not os.path.exists(filepath):
        print(f'File not found: {filepath}')
        return
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    end_idx = len(lines) if end is None else min(end, len(lines))
    print(f'=== {filepath} ({start} to {end_idx} of {len(lines)}) ===')
    for i in range(start - 1, end_idx):
        print(f'{i+1:4d}: {lines[i]}', end='')

def replace_text(filepath, target, replacement, count=1):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if target not in content:
        raise ValueError(f'Target text not found in {filepath}')
    new_content = content.replace(target, replacement, count)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'Successfully replaced in {filepath}')

def write_file(filepath, content):
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Wrote {filepath}')

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'cat':
        s = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        e = int(sys.argv[4]) if len(sys.argv) > 4 else None
        cat(sys.argv[2], s, e)
