import sys
import json
from common import DSLException, DSLWarning, check_metavars_placeholders, check_subterms, check_universal_rule, lint_code

class CSmlException(DSLException):
    """Custom exception for CSml"""
    def __init__(self, message, item=None):
        super().__init__(message, item)

class CSmlWarning(DSLWarning):

    def __init__(self, message, item=None, code=""):
        super().__init__(message, item, code)

def check_semantics(tree):
    """
    Ensures every named placeholder (!xyz) has a corresponding named metavar (?xyz) in a previous predicate. (metavars can be reused in the same predicate)
    Ignores '!!' and '??'.

    subterm checks (need to give some context in order to match it)
        no term can simply be a metavar 
        the rightmost subterm cant have metavars

    if a propagation count is given, checks if its more than 0
    (negatives are not allowed by the grammar, but 0 also does not make sense)
    """
    
    def check_propagate_restricions(tree):
        for dec in tree.find_data("dec"):
            p_restriction = next(dec.find_data("p_restriction"), None)
            if p_restriction:
                restriction = p_restriction.children[0]
                if len(restriction.children) == 1 and int(restriction.children[0].value) == 0:
                    raise CSmlException("Propagation count must be greater than 0.", dec.meta)
                elif len(restriction.children) == 1 and int(restriction.children[0].value) == 1:
                    return [CSmlWarning("Propagation count is 1, consider using 'C' instead for clarity.", dec.meta, code='replace-with-C')]
        return []

    warnings = check_universal_rule(CSmlWarning, tree, "rule", ['loc', 'ins', 'expr', 'mem'])
    warnings += check_metavars_placeholders(CSmlException, CSmlWarning, tree, "rule", ['ins', 'expr', 'mem', 'dec'])
    check_subterms(CSmlException, tree)
    warnings += check_propagate_restricions(tree)

    return warnings

if __name__ == "__main__":
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF
        try:
            data = json.loads(line)
            code = data.get("code", "")
            issues = lint_code(code, "csml.lark", check_semantics)
            print(json.dumps(issues))
            sys.stdout.flush()
        except Exception as e:
            # You can print to stderr for debugging
            sys.stderr.write(f"Error: {str(e)}\n")
            sys.stderr.flush()
            print(json.dumps([]))
            sys.stdout.flush()