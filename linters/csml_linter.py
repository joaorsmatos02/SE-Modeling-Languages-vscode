import os
import re
import sys
import json
from lark import Lark, UnexpectedInput

def check_semantics(rule):
    """
    ensures every named placeholder (!xyz) has a corresponding named metavar (?xyz) in a previous predicate (metavars can be reused in the same predicate)
    if a metavar is unused, suggests replacing with ??
    ignores '!!' and '??'

    subterm checks (need to give some context in order to match it)
        no term can simply be a metavar 
        the rightmost subterm cant have metavars

    checks if propagation count is 1 and suggests switching to C in that case
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
                metavars.add(name)
            else: # is placeholder
                if name not in metavars: # error, placeholder used before being defined
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
    if len(predicates) > 3: # check if its not default
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

    # check propagate
    propagate_pattern = re.compile(r'P\[(\d+)')
    for match in propagate_pattern.finditer(rule):
        value = int(match.group(1))
        column = match.start()
        if value == 1:
            issues.append({
                'column': column,
                'message': "Propagation count is 1, consider using 'C' instead for clarity.",
                'severity': 1,  # warning
                'length': rule.rfind("]") + 1 - match.start(),
                'code': 'replace-with-C'
            })

    return issues

def lint_code(code):
    parser = Lark.open("csml.lark", start="policy", parser="lalr", import_paths=[os.path.dirname(__file__)], rel_to=__file__)
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

    rules = code.split("\n")
    for i in range(len(rules)):
        line_issues = check_semantics(rules[i])
        for issue in line_issues:
            issues.append({**issue, "line": i})

    return issues

if __name__ == "__main__":
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF
        try:
            data = json.loads(line)
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