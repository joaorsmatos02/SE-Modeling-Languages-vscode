import os
import re
from lark import Lark, Token, Tree, UnexpectedInput

def lint_code(code, grammar_file, check_semantics):
    parser = Lark.open(grammar_file, start="policy", parser="lalr", import_paths=[os.path.dirname(__file__)], rel_to=__file__, propagate_positions=True)
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

    try:
        tree = parser.parse(code)
        warnings = check_semantics(tree)
        for warning in warnings:
            issues.append({
                "line": warning.line - 1 if warning.line else 0,
                "column": warning.column - 1 if warning.column else 0,
                "message": warning.message,
                "severity": 1,  # warning
                "length": warning.end_column - warning.column if warning.column else 0,
                "code": warning.code
            })
    except DSLException as e:
        issues.append({
                "line": e.line - 1 if e.line else 0,
                "column": e.column - 1 if e.column else 0,
                "message": e.message,
                "severity": 2,  # error
                "length": e.end_column - e.column  if e.column else 0,
                "code": e.code
            })

    return issues

###################################################################

class DSLException(Exception):

    def __init__(self, message, item=None, code=""):
        super().__init__(message)
        self.message = message
        self.line = item.line if item else None
        self.column = item.column if item else None
        self.end_column = item.end_column if item else None
        self.code = code
        self.source = None

class DSLWarning:

    def __init__(self, message, item=None, code=""):
        self.message = message
        self.line = item.line if item else None
        self.column = item.column if item else None
        self.end_column = item.end_column if item else None
        self.code = code
        self.source = None

# functions for checking semantics of the rules in the DSLs
def check_universal_rule(warning_class, pattern_tree, rule_token, rule_fields):
    warnings = []
    for rule in pattern_tree.find_data(rule_token):
        for field in rule_fields:
            data = next(rule.find_data(field), None)
            if not isinstance(data.children[0].data, Token) or data.children[0].data.value != 'wildcard':
                break
        else:
            end = next(rule.find_data(rule_fields[-1]))
            rule.meta.end_column = end.meta.end_column
            warnings.append(warning_class(f"Universal rule, consider replacing with default.", rule.meta, code="universal-rule"))

    return warnings

def check_metavars_placeholders(exception_class, warning_class, tree, rule_token, rule_fields, starter_metavars={}, used_starter_placeholders=False, distinguished_ok=True):
    warnings = []

    def update_lists(tree, metavars, placeholders):
        this_metavars = []
        for token in tree.scan_values(lambda t: 'NAMED_METAVAR' in t.type):
            if token.value[1:] in metavars and token.value[1:] not in this_metavars:
                raise exception_class(f"Meta-variable '{token.value}' is redefined.", token)
            metavars[token.value[1:]] = token
            this_metavars.append(token.value[1:])
        for token in tree.scan_values(lambda t: 'NAMED_PLACEHOLDER' in t.type):
            if token.value[1:] not in metavars.keys():
                raise exception_class(f"Placeholder '{token.value}' is used before being defined.", token)
            if token.value[1:] in this_metavars:
                raise exception_class(f"Meta-variable '?{token.value[1:]}' is defined and used as '!{token.value[1:]}' in the same predicate. Consider replacing with a meta-variable.", token, "replace-with-?")
            placeholders[token.value[1:]] = token
        if not distinguished_ok and any(tree.scan_values(lambda t: 'DISTINGUISHED_PLACEHOLDER' in t.type)):
            distinguished = next(tree.scan_values(lambda t: 'DISTINGUISHED_PLACEHOLDER' in t.type))
            raise exception_class(f"The distinguished placeholder can only be used if there is an expression predicate", distinguished)

    for rule in tree.find_data(rule_token):
        metavars = {}
        metavars.update(starter_metavars)
        placeholders = {}
        if used_starter_placeholders: # assume they have been used already
            placeholders.update(starter_metavars)

        for field in rule_fields:
            data = next(rule.find_data(field))
            update_lists(data, metavars, placeholders)
            tokens = [t for t in data.scan_values(lambda t: True)]
            if len(tokens) == 1 and ('DISTINGUISHED_PLACEHOLDER' in tokens[0].type or 'ANONYMOUS_METAVAR' in tokens[0].type):
                x = 'distinguished placeholder' if 'DISTINGUISHED_PLACEHOLDER' in tokens[0].type else 'anonymous metavar'
                warnings.append(warning_class(f"Expression predicate simply matches the {x}. Consider replacing with '*'.", data.meta, 'replace-with-*'))

        for key in metavars.keys():
            if key not in placeholders:
                warnings.append(warning_class(f"Named metavar '?{key}' is not used in a placeholder. Consider replacing it with an anonymous metavar.", metavars[key], code="replace-with-??"))

    return warnings

def check_subterms(exception_class, tree):
    term_trees = list(tree.find_pred(lambda t: 'term' in t.data))
    if len(term_trees) != 0:
        rightmost = term_trees[-1].children[-1]
        metavar = next(rightmost.scan_values(lambda t: 'METAVAR' in t.type), None) # FIXME
        if metavar:
            raise exception_class("The rightmost term can't have Metavars in a subexpression chain.", metavar)
        
        for term_tree in term_trees:
            left = term_tree.children[0]
            while isinstance(left, Tree) and len(left.children) == 1:
                left = left.children[0]
            if isinstance(left, Token) and 'METAVAR' in left.type:
                raise exception_class("No term can simply consist of a metavar.", left)
            right = term_tree.children[-1]
            while isinstance(right, Tree) and len(right.children) == 1:
                right = right.children[0]
            if isinstance(right, Token) and 'METAVAR' in right.type:
                raise exception_class("No term can simply consist of a metavar.", right)
