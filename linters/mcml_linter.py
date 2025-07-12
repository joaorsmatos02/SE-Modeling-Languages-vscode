import json
import os
import re
import sys
from lark import Lark, UnexpectedInput

def check_semantics(policy):
    issues = []

    def get_line_column(pos, policy):
        line = policy.count('\n', 0, pos)
        col = pos - policy.rfind('\n', 0, pos) - 1
        return line, col

    def check_single_occurrence(pattern, label, policy, issues):
        matches = list(re.finditer(pattern, policy))
        if len(matches) == 0:
            issues.append({
                'message': f"Policy must contain exactly one {label}.",
                'severity': 2,
                'line': 0,
                'length': 1,
                'column': 0
            })
        if len(matches) > 1:
            lines = policy.splitlines(keepends=True)
            char_count = 0

            # Build a mapping of character index ranges to lines for fast lookup
            line_positions = []
            for line in lines:
                line_positions.append((char_count, char_count + len(line), line))
                char_count += len(line)

            # Flag every occurrence except the first
            for match in matches[1:]:
                pos = match.start()
                for i, (start, end, line) in enumerate(line_positions):
                    if start <= pos < end:
                        issues.append({
                            'message': f"Policy can only have one {label}.",
                            'severity': 2,
                            'line': i,                         # 0-based line index
                            'length': len(line.rstrip('\n')),  # line length without newline
                            'column': pos - start              # column offset within the line
                        })
                        break

    # check if there 1 mc value, as well as 1 static and 1 runtime patterns
    check_single_occurrence(r'mc(\w*):=', 'MC value', policy, issues)
    check_single_occurrence(r'\bstatic\b', 'static pattern', policy, issues)
    check_single_occurrence(r'\bruntime\b', 'runtime pattern', policy, issues)

    # check if pattern names are unique
    pattern_def_matches = set(re.finditer(r'\bpattern\s+(\w+)\s*(?:\((.*?)\))?\s*:', policy))
    for call_match in pattern_def_matches:
        pattern_name = call_match.group(1)
        check_single_occurrence(rf'\bpattern\s+{re.escape(pattern_name)}\s*[:(]', f'pattern named {pattern_name}', policy, issues)
    
    # check if only defined patterns are used and args are passed correctly
    pattern_call_matches = set(re.finditer(r'q\(\s*(\w+)(?:\((.*?)\))?\s*\)', policy))
    for call_match in pattern_call_matches:
        func_name = call_match.group(1)
        args_str = call_match.group(2)
        if args_str is None or args_str.strip() == '':
            arg_count = 0
        else:
            # Split args by comma, handling basic whitespace
            args = [arg.strip() for arg in args_str.split(',') if arg.strip()]
            arg_count = len(args)
        for def_match in pattern_def_matches:
            if def_match.group(1) == func_name:
                # Check if the number of args matches the definition
                if def_match.group(2) is None:
                    expected_arg_count = 0
                else:
                    expected_args = [arg.strip() for arg in def_match.group(2).split(',') if arg.strip()]
                    expected_arg_count = len(expected_args)
                if arg_count != expected_arg_count:
                    line, col = get_line_column(call_match.start(1), policy)
                    issues.append({
                        'message': f"Pattern '{func_name}' expects {expected_arg_count} argument(s), but got {arg_count}.",
                        'severity': 2,
                        'line': line,
                        'length': len(func_name) + len(args_str) + 2, # +2 for the parentheses
                        'column': col
                    })
                break
        else:
            line, col = get_line_column(call_match.start(1), policy)
            issues.append({
                'message': f"Pattern '{func_name}' was called but does not exist.",
                'severity': 2,
                'line': line,
                'length': len(func_name),
                'column': col
            })
    
    return issues

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