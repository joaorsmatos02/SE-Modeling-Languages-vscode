import json
import os
import re
import sys
from lark import Lark, UnexpectedInput

import re

def check_semantics(policy):
    issues = []

    def get_line_column(pos):
        line = policy.count('\n', 0, pos)
        col = pos - policy.rfind('\n', 0, pos) - 1
        return line, col

    def check_single_occurrence(pattern, label):
        matches = list(re.finditer(pattern, policy))
        if len(matches) == 0:
            issues.append({
                'message': f"Policy must contain exactly one {label}.",
                'severity': 2,
                'line': 0,
                'length': 1,
                'column': 0
            })
        elif len(matches) > 1:
            lines = policy.splitlines(keepends=True)
            char_count = 0
            line_positions = []
            for line in lines:
                line_positions.append((char_count, char_count + len(line), line))
                char_count += len(line)

            for match in matches[1:]:
                pos = match.start()
                for i, (start, end, line) in enumerate(line_positions):
                    if start <= pos < end:
                        issues.append({
                            'message': f"Policy can only have one {label}.",
                            'severity': 2,
                            'line': i,
                            'length': len(line.rstrip('\n')),
                            'column': pos - start
                        })
                        break

    # --- Check core pattern declarations ---
    check_single_occurrence(r'mc(\w*):=', 'MC value')
    check_single_occurrence(r'\bstatic\b', 'static pattern')
    check_single_occurrence(r'\bruntime\b', 'runtime pattern')

    # --- Extract pattern definitions and validate uniqueness ---
    pattern_def_matches = list(re.finditer(r'\bpattern\s+(\w+)\s*(?:\((.*?)\))?\s*:', policy))
    for match in pattern_def_matches:
        pattern_name = match.group(1)
        check_single_occurrence(rf'\bpattern\s+{re.escape(pattern_name)}\s*[:(]', f'pattern named {pattern_name}')

    # --- Check pattern calls for validity and argument count ---
    pattern_call_matches = list(re.finditer(r'q\(\s*(\w+)(?:\((.*?)\))?\s*\)', policy))
    for call in pattern_call_matches:
        func_name = call.group(1)
        args_str = call.group(2)
        call_line, col = get_line_column(call.start(1))

        arg_count = 0
        if args_str and args_str.strip():
            args = [arg.strip() for arg in args_str.split(',') if arg.strip()]
            arg_count = len(args)

        for def_match in pattern_def_matches:
            if def_match.group(1) == func_name:
                param_str = def_match.group(2)
                expected_args = [arg.strip() for arg in param_str.split(',')] if param_str else []
                if arg_count != len(expected_args):
                    issues.append({
                        'message': f"Pattern '{func_name}' expects {len(expected_args)} argument(s), but got {arg_count}.",
                        'severity': 2,
                        'line': call_line,
                        'length': len(func_name) + (len(args_str) + 2 if args_str else 0),
                        'column': col
                    })
                break
        else:
            issues.append({
                'message': f"Pattern '{func_name}' was called but does not exist.",
                'severity': 2,
                'line': call_line,
                'length': len(func_name),
                'column': col
            })

    # --- Check for unused patterns ---
    lines = policy.splitlines()
    called_patterns = {m.group(1) for m in pattern_call_matches}
    defined_patterns = {m.group(1) for m in pattern_def_matches}
    unused = defined_patterns - called_patterns - {'static', 'runtime'}

    for pattern in unused:
        for def_match in pattern_def_matches:
            if def_match.group(1) == pattern:
                start_line, _ = get_line_column(def_match.start(1))
                end_line = start_line + 1
                while end_line < len(lines) and not lines[end_line].lstrip().startswith("default"):
                    end_line += 1
                if end_line < len(lines):
                    end_line += 1  # include default line

                for i in range(start_line, end_line):
                    issues.append({
                        'message': f"Pattern '{pattern}' is defined but never called.",
                        'severity': 1,
                        'line': i,
                        'length': len(lines[i].rstrip('\n')),
                        'column': 0
                    })
                break

    # --- Check reserved metavariables and runtime usage ---
    static_match = next((m for m in pattern_def_matches if m.group(1) == 'static'), None)
    runtime_match = next((m for m in pattern_def_matches if m.group(1) == 'runtime'), None)

    if static_match and runtime_match:
        static_line, _ = get_line_column(static_match.start(1))
        runtime_line, _ = get_line_column(runtime_match.start(1))

        saved_placeholders = set()
        i = static_line + 1
        while i < len(lines) and "default" not in lines[i]:
            save = re.search(r'(\S+)\s+>-\s+(\S+)', lines[i])
            if save:
                meta = save.group(2)
                if meta in ["?n", "?c1", "?c2"]:
                    col = lines[i].find(meta)
                    issues.append({
                        'message': f"Static pattern cannot save metavariable '{meta}' as it conflicts with reserved runtime names.",
                        'severity': 2,
                        'line': i,
                        'length': len(meta),
                        'column': col
                    })
                else:
                    saved_placeholders.add(meta[1:])  # strip "?"
            i += 1

        i = runtime_line + 1
        while i < len(lines) and "default" not in lines[i]:
            use = re.search(r'(\S+)\s+-<\s+(\S+)', lines[i])
            if use:
                meta = use.group(2)
                if meta[1:] not in saved_placeholders:
                    col = lines[i].find(meta)
                    issues.append({
                        'message': f"Runtime pattern cannot use metavariable '{meta}' as it was not saved by the static pattern.",
                        'severity': 2,
                        'line': i,
                        'length': len(meta),
                        'column': col
                    })
            i += 1

    return issues

def strip_comments(code):
    lines = code.splitlines()
    return '\n'.join(
        '' if line.strip().startswith('//') else line.split('//', 1)[0].rstrip()
        for line in lines
    )

def lint_code(code):
    parser = Lark.open("mcml.lark", start="policy", parser="lalr", import_paths=[os.path.dirname(__file__)], rel_to=__file__)
    issues = []
    try:
        parser.parse(code)
    except UnexpectedInput as e:
        err = re.findall(r"'([^']*)'", str(e))[1]
        issues.append({
            "line": e.line - 1,
            "column": e.column - 1,
            "message": str(e).split("\\n")[0].split("//")[0].rstrip(),
            "severity": 2,  # error
            "length": len(err.split(" ")[0])
        })
        return issues

    code = strip_comments(code)
    issues += check_semantics(code)

    return issues

if __name__ == "__main__":
    while True:
        policy = sys.stdin.readline()
        if not policy:
            break  # EOF
        try:
            data = json.loads(policy)
            code = data.get("code", "")
            issues = lint_code(code)
            print(json.dumps(issues))
            sys.stdout.flush()
        except Exception as e:
            # You can print to stderr for debugging
            sys.stderr.write(f"Error: {str(e)}\n")
            sys.stderr.flush()
            print(json.dumps([]))
            sys.stdout.flush()