import json
import os
import re
import sys
from lark import Lark, UnexpectedInput

def check_semantics(policy): #TODO se houver uma regra so de wildcards dizer q o resto é unreachable - sugerir trocar por default e apagar o resto
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

    # --- Check for unused patterns --- TODO suggest deletion
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

    # --- Check usage of metavars and placeholders (akin to csml) ---
    for pattern in pattern_def_matches:
        if pattern.group(1) != 'runtime':
            start, _ = get_line_column(pattern.start(1))
            start += 1  # skip the pattern definition line
            lines = policy.splitlines()

            # extract args
            param_str = pattern.group(2)
            args = [arg.strip()[1:] for arg in param_str.split(',')] if param_str else []

            end = start
            while not "default" in lines[end]:
                end += 1

            used_placeholders = set()
            for i in range(start, end):
                line_placeholders, line_issues = check_semantics_2(lines[i], args)  # pass args to the function
                used_placeholders.update(line_placeholders)
                for issue in line_issues:
                    issues.append({**issue, "line": i})
            unused_args = set(args) - used_placeholders
            for arg in unused_args: # check for unused args
                issues.append({
                    'message': f"Argument '{arg}' is defined but never used in the pattern.",
                    'severity': 1,
                    'line': start-1,
                    'length': len(arg) + 1,  # include the "?" prefix
                    'column': lines[start-1].find(f"?{arg}")
                })

    return issues

def check_semantics_2(rule, args):
    """
    ensures every named placeholder (!xyz) has a corresponding named metavar (?xyz) in a previous predicate (metavars can be reused in the same predicate)
    if a metavar is unused, suggests replacing with ??
    ignores '!!' and '??'

    subterm checks (need to give some context in order to match it)
        no term can simply be a metavar 
        the rightmost subterm cant have metavars
    """
    issues = []
    predicates = rule.split("::")

    # check metavars and placeholders
    variables_pattern = re.compile(r'[?!][a-zA-Z]+')
    metavars = set()
    placeholders = set()
    for predicate in predicates:
        variables = list(variables_pattern.finditer(predicate))
        for match in reversed(variables):
            symbol = match.group()
            name = symbol[1:]
            if symbol.startswith('?'): # is metavar
                if name in args:  # if it's an argument, we treat it as a metavar
                    issues.append({
                        'column': rule.find(symbol),
                        'message': f"Metavar ?{name} is received as an argument and is being redefined.",
                        'severity': 1,
                        'length': len(symbol)
                    })
                metavars.add(name)
            else: # is placeholder
                if name not in metavars and name not in args: # error, placeholder used before being defined
                    issues.append({
                        'column': rule.find(symbol),
                        'message': f"Placeholder {symbol} is used before being defined.",
                        'severity': 2,  # error
                        'length': len(symbol)
                    })
                else:
                    placeholders.add(name)
    unused_metavars = metavars - placeholders
    for var in unused_metavars:
        issues.append({
            'column': rule.find("?"+var),
            'message': f"Metavar ?{var} is not used in a placeholder. Consider replacing it with an anonymous metavar.",
            'severity': 1,  # warning
            'length': 1+len(var),
            'code': 'replace-with-??'
        })

    # check subterms
    if len(predicates) > 3: # check if its not default and has expr
        previous_len = len(predicates[0]) + len(predicates[1]) + 4
        expr_pattern = predicates[2]
        if "=<" in expr_pattern or "-<" in expr_pattern:
            subterms = re.split(r'(?:=<|-<)', expr_pattern)
            if re.search(r'\?([a-zA-Z]+|\?)', subterms[-1]): # error, rightmost subterm cant have metavars
                issues.append({
                    'column': previous_len + len(predicates[2]) - len(subterms[-1].rstrip()),
                    'message': f"The rightmost subterm can't have metavars.",
                    'severity': 2,  # error
                    'length': len(subterms[-1].strip()),
                })
            for term in subterms[:-1]:
                term = term.strip()
                if re.match(r'\?([a-zA-Z]+|\?)', term): # error, no term can simply be a metavar
                    issues.append({
                        'column': previous_len + predicates[2].find(term),
                        'message': f"No term can simply consist of a metavar.",
                        'severity': 2,  # error
                        'length': len(term),
                    })
    
    return placeholders, issues

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